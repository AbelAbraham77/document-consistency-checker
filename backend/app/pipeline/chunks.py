"""Independently callable, idempotent chunk extraction; no HTTP/queue dependency."""

from uuid import UUID
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.session import create_database_engine
from app.database.repositories import bulk_insert_claims
from app.extraction.local import LocalFactExtractor as default_service
from app.models import Chunk
from app.parsing import DocumentChunk
from app.schemas.persistence import ClaimCreate


def persist_extracted_claims(session: Session, document_id: UUID, chunk_id: UUID, claims):
    """Upsert exact extraction results; caller commits claims + checkpoint together."""
    items = []
    for claim in claims:
        values = claim.model_dump()
        # LLM confidence has arbitrary precision; the persistence schema has four places.
        if values["confidence"] is not None:
            values["confidence"] = values["confidence"].quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        items.append(ClaimCreate(chunk_id=chunk_id, **values))
    bulk_insert_claims(session, document_id, items)


def extract_chunk(session: Session, document_id: UUID, chunk_id: UUID, *, extractor_factory=default_service) -> bool:
    """Owns one transaction. Return False for an already completed chunk.

    PostgreSQL row locking serializes duplicate deliveries across processes.
    The lock currently spans bounded LLM attempts; a future leased task with a
    fencing token can shorten transactions without weakening duplicate safety.
    Each concurrently executed chunk MUST receive its own Session/connection.
    """
    try:
        chunk = session.scalar(select(Chunk).where(Chunk.id == chunk_id, Chunk.document_id == document_id)
                               .with_for_update().execution_options(populate_existing=True))
        if chunk is None:
            raise ValueError("Chunk does not belong to this document")
        if chunk.extraction_status == "COMPLETED":
            session.commit()
            return False
        parsed = DocumentChunk.model_validate(chunk.parsed_data)
        response = extractor_factory().extract(parsed)
        persist_extracted_claims(session, document_id, chunk_id, response.claims)
        chunk.extraction_status = "COMPLETED"  # Empty successful extraction is also a checkpoint.
        session.commit()
        return True
    except Exception:
        session.rollback()
        # Do not write FAILED here: a commit may have succeeded but lost its ACK.
        # Redelivery re-reads the durable checkpoint and unique extraction keys.
        raise


def process_chunk(document_id: UUID, chunk_id: UUID, *, settings: Settings | None = None) -> bool:
    """Worker entry point. Accept only internal queue jobs."""
    engine = create_database_engine(settings or Settings())
    if engine is None:
        raise ValueError("DATABASE_URL is required")
    try:
        with Session(engine, expire_on_commit=False) as session:
            return extract_chunk(session, document_id, chunk_id)
    finally:
        engine.dispose()
