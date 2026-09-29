"""Persistence models; importing them performs no database operations."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    JSON, Numeric, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.vector_types import EMBEDDING_DIMENSIONS


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (CheckConstraint("page_count >= 0", name="page_count_nonnegative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    filename: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(64), default="uploaded", server_default="uploaded", index=True)
    processing_state: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("page_start >= 1 AND page_end >= page_start", name="valid_page_range"),
        UniqueConstraint("document_id", "id", name="uq_chunks_document_id_id"),
        Index("ix_chunks_document_pages", "document_id", "page_start", "page_end"),
        Index("ix_chunks_content_hash", "content_hash"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    parsed_data: Mapped[dict | None] = mapped_column(JSON)
    extraction_status: Mapped[str] = mapped_column(String(32), default="PENDING", server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("document_id", "id", name="uq_claims_document_id_id"),
        UniqueConstraint("chunk_id", "extraction_key", name="uq_claims_chunk_extraction_key"),
        ForeignKeyConstraint(["document_id", "chunk_id"], ["chunks.document_id", "chunks.id"],
                             name="fk_claims_document_chunk", ondelete="CASCADE"),
        CheckConstraint("source_page >= 1", name="source_page_positive"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        Index("ix_claims_candidate_keys", "document_id", "entity_normalized", "metric_normalized", "period"),
        Index("ix_claims_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    entity_raw: Mapped[str] = mapped_column(Text)
    entity_normalized: Mapped[str | None] = mapped_column(String(255), index=True)
    metric_raw: Mapped[str] = mapped_column(Text)
    metric_normalized: Mapped[str | None] = mapped_column(String(255), index=True)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric())
    unit: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[str | None] = mapped_column(String(128), index=True)
    scope: Mapped[str | None] = mapped_column(Text)
    qualifier: Mapped[str | None] = mapped_column(Text)
    claim_text: Mapped[str] = mapped_column(Text)
    extraction_key: Mapped[str | None] = mapped_column(String(64))
    source_page: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS).with_variant(JSON(), "sqlite"))
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    embedding_text_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contradiction(Base):
    __tablename__ = "contradictions"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "claim_a_id"], ["claims.document_id", "claims.id"],
                             name="fk_contradictions_claim_a", ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id", "claim_b_id"], ["claims.document_id", "claims.id"],
                             name="fk_contradictions_claim_b", ondelete="CASCADE"),
        CheckConstraint("claim_a_id < claim_b_id", name="canonical_distinct_pair"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        UniqueConstraint("document_id", "claim_a_id", "claim_b_id", name="uq_contradictions_pair"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    claim_a_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    claim_b_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    classification: Mapped[str] = mapped_column(String(64))
    explanation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(String(32), default="candidate", server_default="candidate", index=True)
    source: Mapped[str] = mapped_column(String(16), default="gemini", server_default=text("'gemini'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
