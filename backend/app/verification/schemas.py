"""Strict, closed classification vocabulary for final pair verification."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Classification = Literal[
    "CONTRADICTION", "CONSISTENT", "TEMPORAL_DIFFERENCE", "SCOPE_DIFFERENCE",
    "DEFINITION_DIFFERENCE", "REPORTING_BASIS_DIFFERENCE", "ROUNDING_DIFFERENCE",
    "UNRELATED", "INSUFFICIENT_CONTEXT",
    "UNCERTAIN",
]
VerdictSource = Literal["rules", "gemini"]


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    classification: Classification
    explanation: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)
    source: VerdictSource = "gemini"

    @field_validator("explanation")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Explanation must contain evidence-based text")
        return value


class BatchVerificationItem(VerificationResult):
    candidate_id: str = Field(min_length=1, max_length=100)


class BatchVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    results: list[BatchVerificationItem]


def validate_verification_response(raw: str) -> VerificationResult:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("Non-finite JSON number")

    return VerificationResult.model_validate(json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant))


def verification_schema() -> dict:
    schema = VerificationResult.model_json_schema()
    # Length constraints are enforced locally, keeping the provider schema portable.
    explanation = schema["properties"]["explanation"]
    explanation.pop("minLength", None)
    explanation.pop("maxLength", None)
    return schema
