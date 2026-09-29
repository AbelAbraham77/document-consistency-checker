"""Validate response payloads without repairing, extracting, or calling an LLM."""

from decimal import Decimal
import json
from typing import Any

from app.extraction.schemas import ClaimExtractionResponse


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-standard JSON number: {value}")


def validate_claim_extraction_response(payload: str | dict[str, Any]) -> ClaimExtractionResponse:
    """Validate JSON or a decoded object; preserve decimal precision in JSON.

    Malformed JSON (including duplicate keys/NaN) raises ValueError. Invalid
    schemas raise Pydantic ValidationError with locations such as
    ('claims', 0, 'source_page'). No bad claims are silently dropped.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(
                payload, parse_float=Decimal, parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid claim extraction JSON: {exc}") from exc
    return ClaimExtractionResponse.model_validate(payload)
