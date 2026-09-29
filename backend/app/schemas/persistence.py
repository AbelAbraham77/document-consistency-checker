"""Validated repository inputs; no HTTP endpoints are attached to these schemas."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.database.vector_types import EMBEDDING_DIMENSIONS


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DocumentCreate(WriteModel):
    filename: str = Field(min_length=1)
    storage_path: str | None = None
    page_count: int = Field(default=0, ge=0)
    status: str = Field(default="uploaded", min_length=1, max_length=32)


class ChunkCreate(WriteModel):
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section: str | None = None
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_pages(self):
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self


class ClaimCreate(WriteModel):
    chunk_id: UUID
    entity_raw: str = Field(min_length=1)
    entity_normalized: str | None = Field(default=None, max_length=255)
    metric_raw: str = Field(min_length=1)
    metric_normalized: str | None = Field(default=None, max_length=255)
    value_text: str | None = None
    value_numeric: Decimal | None = None
    unit: str | None = Field(default=None, max_length=64)
    period: str | None = Field(default=None, max_length=128)
    scope: str | None = None
    qualifier: str | None = None
    claim_text: str = Field(min_length=1)
    source_page: int = Field(ge=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    embedding: list[float] | None = Field(default=None, min_length=EMBEDDING_DIMENSIONS, max_length=EMBEDDING_DIMENSIONS)


class ContradictionCreate(WriteModel):
    claim_a_id: UUID
    claim_b_id: UUID
    classification: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1, decimal_places=4)
    status: str = Field(default="candidate", min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_claims(self):
        if self.claim_a_id == self.claim_b_id:
            raise ValueError("A contradiction requires two distinct claims")
        return self
