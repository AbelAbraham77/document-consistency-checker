"""Public HTTP contracts; never expose filesystem paths, vectors or provider keys."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentRead(ORMResponse):
    id: UUID
    filename: str
    page_count: int
    status: str
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    document_id: UUID
    filename: str
    page_count: int
    status: str


class ProcessResponse(BaseModel):
    document_id: UUID
    status: str = "QUEUED"


class ProgressCounts(BaseModel):
    pages: int = 0
    pages_processed: int | None = None
    page_batch_size: int | None = None
    total_page_batches: int | None = None
    chunks: int = 0
    chunks_completed: int = 0
    chunks_failed: int = 0
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


class ProcessingError(BaseModel):
    stage: str
    item_id: str
    error_type: str
    message: str
    category: str | None = None
    retryable: bool = False
    attempts: int = 0
    http_status: int | None = None
    provider: str | None = None


class ProgressResponse(BaseModel):
    document_id: UUID
    status: str
    stage: str
    counts: ProgressCounts
    updated_at: datetime
    errors: list[ProcessingError] = Field(default_factory=list)


class ClaimRead(ORMResponse):
    id: UUID
    document_id: UUID
    chunk_id: UUID
    entity_raw: str
    entity_normalized: str | None
    metric_raw: str
    metric_normalized: str | None
    value_text: str | None
    value_numeric: Decimal | None
    unit: str | None
    period: str | None
    scope: str | None
    qualifier: str | None
    claim_text: str
    source_page: int
    confidence: Decimal | None
    created_at: datetime


class ClaimPage(BaseModel):
    items: list[ClaimRead]
    total: int
    offset: int
    limit: int


class ContradictionRead(ORMResponse):
    id: UUID
    document_id: UUID
    claim_a_id: UUID
    claim_b_id: UUID
    classification: str
    explanation: str
    confidence: Decimal
    status: str
    source: str
    created_at: datetime


class SourceContext(ORMResponse):
    id: UUID
    page_start: int
    page_end: int
    section: str | None
    text: str


class ClaimWithContext(ClaimRead):
    context: SourceContext


class ContradictionDetail(ContradictionRead):
    claim_a: ClaimWithContext
    claim_b: ClaimWithContext
