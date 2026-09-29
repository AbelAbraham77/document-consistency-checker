"""JSON-serializable worker messages and a replaceable execution interface."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProcessDocumentJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    document_id: UUID


class JobExecutor(Protocol):
    def submit(self, job: ProcessDocumentJob) -> None:
        """Accept a job without waiting for document processing, or raise."""
        ...

    def close(self) -> None: ...


class ProcessChunkJob(BaseModel):
    """Future fan-out payload; emitted only after the chunk manifest commits."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    document_id: UUID
    chunk_id: UUID


class JobOutcome(BaseModel):
    """Broker adapters map retryable outcomes to retry/NACK rather than success."""
    status: Literal["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "BUSY", "CANCELLED"]
    retryable: bool = False
