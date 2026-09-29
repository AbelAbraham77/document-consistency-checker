"""Local relevance scoring before final reasoning. No model calls."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.matching.structured import _period_relation, _unit_relation, _scaled


class CascadeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    candidate_score_threshold: float = Field(default=0.65, ge=0, le=1)
    verification_batch_size: int = Field(default=8, ge=1, le=32)
    verification_request_budget: int = Field(default=1, ge=0)


def suspicion_score(pair, similarity=0.0):
    a, b = pair.claim_a, pair.claim_b
    unit, sa, sb = _unit_relation(a, b)
    va, vb = a.original.value_numeric, b.original.value_numeric
    if va is not None and vb is not None:
        if unit == "same" and _scaled(va, sa) == _scaled(vb, sb):
            return 0.0
        if unit == "unknown" and va == vb:
            return 0.0
    elif a.original.value_text == b.original.value_text:
        return 0.0
    period = _period_relation(a.period, b.period)
    scopes = [c.original.scope.lower().strip() if c.original.scope else None for c in (a, b)]
    # Clear local explanations need no paid reasoning.
    if period == "temporal" or (all(scopes) and scopes[0] != scopes[1]):
        return 0.0
    score = 0.25 * (a.entity_normalized == b.entity_normalized and a.entity_normalized != "document subject")
    score += 0.30 * (a.metric_normalized == b.metric_normalized)
    score += 0.15 * (period == "same")
    score += 0.10 * (bool(scopes[0]) and scopes[0] == scopes[1])
    score += 0.10  # Values differ; units/basis still require the final verifier.
    score += 0.25 * max(0.0, min(1.0, similarity))
    return min(1.0, score)
