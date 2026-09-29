"""Stable per-claim text format and content fingerprint, independent of provenance."""

import hashlib
import json

from app.models import Claim
from app.normalization import normalize_entity, normalize_metric, normalize_period, normalize_unit

TEXT_VERSION = "claim-context-v1"


def embedding_text(claim: Claim) -> str:
    def clean(value):
        return value.replace("\r\n", "\n").replace("\r", "\n").strip() if isinstance(value, str) else value

    unit = normalize_unit(claim.unit)
    period = normalize_period(claim.period)
    context = {
        "claim_text": clean(claim.claim_text),
        "entity": clean(claim.entity_normalized) or normalize_entity(claim.entity_raw),
        "metric": clean(claim.metric_normalized) or normalize_metric(claim.metric_raw),
        "unit": unit.canonical if unit else None,
        "period": period.canonical if period else None,
        "scope": clean(claim.scope), "qualifier": clean(claim.qualifier),
    }
    if not context["claim_text"]:
        raise ValueError("Cannot embed empty claim text")
    return json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
