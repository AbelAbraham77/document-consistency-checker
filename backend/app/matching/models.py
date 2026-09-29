"""Preliminary pair classifications, not verified contradictions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.normalization.models import NormalizedClaim

CandidateClassification = Literal[
    "candidate_contradiction", "possible_scope_difference", "temporal_difference",
    "possible_period_difference", "possible_unit_difference", "possible_qualifier_difference",
    "insufficient_context", "possible_value_difference",
]


class CandidatePair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_a: NormalizedClaim
    claim_b: NormalizedClaim
    reason_for_candidate: str
    preliminary_classification: CandidateClassification
