"""Development implementation of JobExecutor; no HTTP or database dependencies."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import logging

from app.jobs.contracts import ProcessDocumentJob

logger = logging.getLogger(__name__)


class InProcessExecutor:
    def __init__(self, handler, workers=1):
        self.handler = handler
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="document-job")
        self.lock = Lock()
        self.pending = set()

    def submit(self, job: ProcessDocumentJob) -> None:
        with self.lock:
            if job.document_id in self.pending:
                raise RuntimeError("Document is already queued")
            self.pending.add(job.document_id)
        try:
            self.pool.submit(self._execute, job)
        except Exception:
            with self.lock:
                self.pending.discard(job.document_id)
            raise

    def _execute(self, job):
        try:
            logger.info("Document job started: document=%s", job.document_id)
            self.handler(job)
        finally:
            with self.lock:
                self.pending.discard(job.document_id)

    def close(self):
        self.pool.shutdown(wait=True)
