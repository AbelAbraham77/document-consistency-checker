"""Durable queue status and dispatch, with no FastAPI dependencies."""

from sqlalchemy import update

from app.jobs.contracts import JobExecutor, ProcessDocumentJob
from app.models import Document

ACTIVE_STAGES = {"QUEUED", "PARSING", "CHUNKING", "EXTRACTING_CLAIMS", "NORMALIZING", "GENERATING_CANDIDATES", "VERIFYING"}


class JobConflict(ValueError):
    pass


class JobDispatchError(RuntimeError):
    pass


def enqueue_document(session, document: Document, executor: JobExecutor):
    if document.status in ACTIVE_STAGES or document.status == "COMPLETED":
        raise JobConflict("Document is already processing or completed")
    if not document.storage_path:
        raise JobConflict("The document's stored PDF is unavailable")
    result = session.execute(update(Document).where(Document.id == document.id,
        Document.status.not_in([*ACTIVE_STAGES, "COMPLETED"])).values(status="QUEUED"))
    if result.rowcount != 1:
        session.rollback()
        raise JobConflict("Document is already processing or completed")
    document_id = document.id
    session.commit()  # The worker must see the committed document before dispatch.
    try:
        executor.submit(ProcessDocumentJob(document_id=document_id))
    except Exception:
        document.status = "FAILED"
        document.processing_state = {**(document.processing_state or {}), "errors": [{
            "stage": "QUEUED", "item_id": str(document_id), "error_type": "SchedulingError",
            "message": "Processing could not be scheduled; retry the request.",
        }]}
        session.commit()
        raise JobDispatchError("Processing could not be scheduled; retry") from None
