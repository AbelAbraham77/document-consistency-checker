"""Resolve original claims and full source chunks without guessing identity by text."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.schemas import Claim as ClaimSchema
from app.matching.models import CandidatePair
from app.models import Claim, Chunk
from app.normalization import normalize_stored_claim


@dataclass
class PairContext:
    claim_a: Claim
    claim_b: Claim
    evidence: dict
    insufficient_reason: str | None = None


def candidate_from_claims(claim_a: Claim, claim_b: Claim) -> CandidatePair:
    """Build a verification-ready pair, including for semantic retrieval matches."""
    if claim_a.id == claim_b.id or claim_a.document_id != claim_b.document_id:
        raise ValueError("Candidates require two distinct claims from the same document")
    return CandidatePair(claim_a=normalize_stored_claim(claim_a), claim_b=normalize_stored_claim(claim_b),
                         reason_for_candidate="Pair selected for verification",
                         preliminary_classification="possible_value_difference")


def load_pair_context(session: Session, candidate: CandidatePair) -> PairContext:
    a, b = candidate.claim_a, candidate.claim_b
    if (a.document_id is None or a.document_id != b.document_id or a.claim_id is None
            or b.claim_id is None or a.claim_id == b.claim_id):
        raise ValueError("Verification requires distinct persisted claim IDs in the same document")
    # Model labels must match the canonical order saved in the contradictions table.
    if a.claim_id > b.claim_id:
        a, b = b, a
    records, evidence = [], {}
    insufficient = None
    for label, normalized in (("claim_a", a), ("claim_b", b)):
        stored = session.scalar(select(Claim).where(Claim.id == normalized.claim_id,
                                                   Claim.document_id == normalized.document_id))
        if stored is None or stored.chunk_id != normalized.chunk_id:
            raise ValueError("Candidate source identity does not match storage")
        original = {name: getattr(stored, name) for name in ClaimSchema.model_fields}
        if ClaimSchema.model_validate(original) != normalized.original:
            raise ValueError("Candidate claim is stale; regenerate it from current storage")
        chunk = session.scalar(select(Chunk).where(Chunk.id == stored.chunk_id,
                                                   Chunk.document_id == stored.document_id))
        if chunk is None or not chunk.text.strip():
            insufficient = "An original source chunk is missing or empty; the pair cannot be verified."
        elif not chunk.page_start <= stored.source_page <= chunk.page_end or stored.claim_text not in chunk.text:
            insufficient = "A claim quote or page is not grounded in its original source chunk."
        evidence[label] = {
            "original_claim": normalized.original.model_dump(mode="json"),
            "normalized_context": normalized.model_dump(mode="json", exclude={"original", "claim_id", "document_id", "chunk_id"}),
            "source_context": None if chunk is None else {
                "text": chunk.text, "section": chunk.section,
                "page_start": chunk.page_start, "page_end": chunk.page_end,
            },
        }
        records.append(stored)
    return PairContext(records[0], records[1], evidence, insufficient)
