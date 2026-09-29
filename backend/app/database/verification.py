"""Canonical upserts and explicit visibility filtering for verified results."""

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Claim, Contradiction
from app.verification.schemas import VerificationResult


def save_verification_result(session: Session, *, document_id: UUID, claim_a_id: UUID,
                             claim_b_id: UUID, result: VerificationResult) -> Contradiction:
    if claim_a_id == claim_b_id:
        raise ValueError("Cannot verify a claim against itself")
    a, b = sorted((claim_a_id, claim_b_id))
    classification = result.classification
    status = ("verified" if classification == "CONTRADICTION" else
              "dismissed" if classification in {"CONSISTENT", "UNRELATED"} else
              "needs_review" if classification == "INSUFFICIENT_CONTEXT" else "contextual_difference")
    fields = dict(classification=classification, explanation=result.explanation,
                  confidence=Decimal(str(result.confidence)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
                  status=status, source=result.source)
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Verification persistence requires PostgreSQL (or SQLite for tests)")
    statement = insert(Contradiction).values(id=uuid4(), document_id=document_id, claim_a_id=a, claim_b_id=b, **fields)
    statement = statement.on_conflict_do_update(
        index_elements=["document_id", "claim_a_id", "claim_b_id"], set_=fields,
    ).returning(Contradiction)
    return session.scalars(statement, execution_options={"populate_existing": True}).one()


def get_visible_contradictions(session: Session, document_id: UUID) -> list[Contradiction]:
    claim_a = aliased(Claim)
    claim_b = aliased(Claim)
    return list(session.scalars(select(Contradiction).where(
        Contradiction.document_id == document_id, Contradiction.status == "verified",
        Contradiction.classification == "CONTRADICTION",
    ).join(claim_a, claim_a.id == Contradiction.claim_a_id).join(
        claim_b, claim_b.id == Contradiction.claim_b_id,
    ).where(
        claim_a.document_id == document_id,
        claim_b.document_id == document_id,
        claim_a.entity_normalized == claim_b.entity_normalized,
        claim_a.metric_normalized == claim_b.metric_normalized,
    ).order_by(Contradiction.created_at, Contradiction.id)))
