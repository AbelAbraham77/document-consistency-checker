"""Worker entry point usable by in-process, Celery or message-queue adapters."""

import logging
from sqlalchemy import select

from app.jobs.contracts import ProcessDocumentJob, JobOutcome
from app.models import Document
from app.pipeline.models import DocumentAlreadyProcessing

logger = logging.getLogger(__name__)


class DocumentJobWorker:
    def __init__(self, session_factory_provider, processor, storage_provider):
        self.session_factory_provider = session_factory_provider
        self.processor = processor
        self.storage_provider = storage_provider

    def __call__(self, job: ProcessDocumentJob):
        factory = self.session_factory_provider()
        try:
            with factory() as session:
                document = session.get(Document, job.document_id)
                if document is None:
                    return JobOutcome(status="FAILED", retryable=False)
                if document.status == "CANCELLED":
                    return JobOutcome(status="CANCELLED", retryable=False)
                if document.status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
                    return JobOutcome(status=document.status, retryable=False)
                path = document.storage_path
                if not path:
                    raise ValueError("Document has no stored source")
            # The worker owns context acquisition; no ORM objects/request sessions cross this boundary.
            with self.storage_provider().retrieve(path) as local_path:
                result = self.processor(str(local_path), job.document_id)
            status = getattr(result, "status", "COMPLETED")
            logger.info("Document job completed: document=%s status=%s pages_processed=%s",
                        job.document_id, status,
                        getattr(result, "pages_processed", "unknown"))
            return JobOutcome(status=status, retryable=status in {"FAILED", "COMPLETED_WITH_WARNINGS"})
        except DocumentAlreadyProcessing:
            logger.info("Duplicate job skipped; document is already processing: %s", job.document_id)
            return JobOutcome(status="BUSY", retryable=True)
        except Exception as exc:
            logger.error("Document job failed: document=%s category=%s", job.document_id, type(exc).__name__)
            try:
                with factory.begin() as session:
                    # Serialize the status check with concurrent stage/completion writes.
                    document = session.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
                    if document is not None and document.status in {
                        "QUEUED", "UPLOADED", "uploaded", "FAILED", "COMPLETED_WITH_WARNINGS"
                    }:
                        document.status = "FAILED"
                        state = document.processing_state or {}
                        document.processing_state = {**state, "errors": [*state.get("errors", []), {
                            "stage": "BACKGROUND_PROCESSING", "item_id": str(job.document_id),
                            "error_type": type(exc).__name__, "message": "Background processing failed; check source and service configuration, then retry.",
                        }]}
            except Exception:
                logger.error("Could not persist job failure: document=%s", job.document_id)
                raise
            return JobOutcome(status="FAILED", retryable=True)
