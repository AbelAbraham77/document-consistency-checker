"""Repository operations flush/execute but never commit; callers own transactions."""

from collections.abc import Sequence
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from sqlalchemy import insert, select, tuple_
from sqlalchemy.orm import Session

from app.models import Claim, Chunk, Contradiction, Document
from app.schemas.persistence import ClaimCreate, ChunkCreate, ContradictionCreate, DocumentCreate


def create_document(session: Session, data: DocumentCreate) -> Document:
    document = Document(**data.model_dump())
    session.add(document)
    session.flush()
    return document


def _bulk_insert(session: Session, model, rows: list[dict]):
    if not rows:
        return []
    # Explicit UUIDs let us restore input order without requiring ordered RETURNING.
    rows = [{"id": uuid4(), **row} for row in rows]
    result = list(session.scalars(insert(model).returning(model), rows))
    by_id = {item.id: item for item in result}
    return [by_id[row["id"]] for row in rows]


def bulk_insert_chunks(session: Session, document_id: UUID, chunks: Sequence[ChunkCreate]) -> list[Chunk]:
    return _bulk_insert(session, Chunk, [
        {**chunk.model_dump(), "document_id": document_id,
         "content_hash": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()}
        for chunk in chunks
    ])


def get_chunks_by_document(session: Session, document_id: UUID) -> list[Chunk]:
    return list(session.scalars(select(Chunk).where(Chunk.document_id == document_id)
                               .order_by(Chunk.page_start, Chunk.page_end, Chunk.created_at, Chunk.id)))


def bulk_insert_claims(session: Session, document_id: UUID, claims: Sequence[ClaimCreate]) -> list[Claim]:
    rows = []
    for claim in claims:
        values = claim.model_dump()
        if values["confidence"] is not None:
            values["confidence"] = values["confidence"].quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        item = ClaimCreate(**values)
        identity = json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        rows.append({**item.model_dump(), "id": uuid4(), "document_id": document_id,
                     "extraction_key": hashlib.sha256(identity.encode()).hexdigest()})
    return _insert_once(session, Claim, document_id, rows, ("chunk_id", "extraction_key"))


def _insert_once(session, model, document_id, rows, keys):
    """Database uniqueness handles simultaneous retries; preserve existing results."""
    if not rows:
        return []
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        raise ValueError("Retry-safe persistence requires PostgreSQL")
    unique = {tuple(row[key] for key in keys): row for row in rows}
    # Batches avoid parameter limits on large documents.
    identities = list(unique)
    existing = {}
    for offset in range(0, len(identities), 200):
        batch = identities[offset:offset + 200]
        session.execute(dialect_insert(model).values([unique[key] for key in batch])
                        .on_conflict_do_nothing(index_elements=list(keys)))
        records = session.scalars(select(model).where(model.document_id == document_id,
            tuple_(*(getattr(model, key) for key in keys)).in_(batch)))
        existing.update({tuple(getattr(record, key) for key in keys): record for record in records})
    return [existing[tuple(row[key] for key in keys)] for row in rows]


def get_claims_by_document(session: Session, document_id: UUID) -> list[Claim]:
    return list(session.scalars(select(Claim).where(Claim.document_id == document_id)
                               .order_by(Claim.source_page, Claim.created_at, Claim.id)))


def save_contradiction_candidates(
    session: Session, document_id: UUID, candidates: Sequence[ContradictionCreate],
) -> list[Contradiction]:
    rows = []
    for candidate in candidates:
        row = candidate.model_dump()
        row["claim_a_id"], row["claim_b_id"] = sorted((candidate.claim_a_id, candidate.claim_b_id))
        rows.append({**row, "id": uuid4(), "document_id": document_id})
    return _insert_once(session, Contradiction, document_id, rows, ("document_id", "claim_a_id", "claim_b_id"))
