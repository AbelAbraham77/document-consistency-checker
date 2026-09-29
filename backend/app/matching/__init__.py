"""Deterministic candidate generation; classifications require later verification."""

from app.matching.models import CandidatePair
from app.matching.structured import generate_structured_candidates

__all__ = ["CandidatePair", "generate_structured_candidates"]
