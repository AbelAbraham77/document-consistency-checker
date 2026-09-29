from uuid import UUID

from pydantic import BaseModel, Field


class DocumentAlreadyProcessing(RuntimeError):
    """Another worker owns the document's processing lock."""


class ProcessingSummary(BaseModel):
    document_id: UUID
    status: str
    pages: int = 0
    pages_processed: int = 0
    page_batch_size: int = 25
    total_page_batches: int = 0
    chunks: int = 0
    claims: int = 0
    deterministic_candidates: int = 0
    semantic_candidates: int = 0
    embeddings_completed: int = 0
    embeddings_failed: int = 0
    locally_extracted_facts: int = 0
    candidates_before_filtering: int = 0
    candidates_after_filtering: int = 0
    gemini_requests: int = 0
    candidates_pending: int = 0
    candidates_total: int = 0
    candidates_verified: int = 0
    candidates_failed: int = 0
    verified_contradictions: int = 0
    errors: list[dict[str, str | int | bool | None]] = Field(default_factory=list)
