"""Atomic extraction contracts, independent of ORM records and LLM providers."""

from datetime import date
from decimal import Decimal, InvalidOperation
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)


class Claim(BaseModel):
    """One entity/metric/value assertion with its original text and source page.

    Normalized names are supplied by the caller; this schema does not infer them.
    Decimal fields accept JSON numbers or plain decimal strings, never booleans,
    formatted currency, percentages, NaN, or infinity.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    entity_raw: str = Field(min_length=1)
    entity_normalized: str | None = Field(min_length=1, max_length=255)
    metric_raw: str = Field(min_length=1)
    metric_normalized: str | None = Field(min_length=1, max_length=255)
    value_text: str = Field(min_length=1)
    value_numeric: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    period: str | None = Field(default=None, min_length=1, max_length=128)
    scope: str | None = Field(default=None, min_length=1)
    qualifier: str | None = Field(default=None, min_length=1)
    claim_text: str = Field(min_length=1)
    source_page: int = Field(ge=1, description="Physical, one-based PDF page number")
    confidence: Decimal | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "entity_raw", "entity_normalized", "metric_raw", "metric_normalized",
        "value_text", "unit", "period", "scope", "qualifier", "claim_text",
    )
    @classmethod
    def nonblank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must contain non-whitespace text; use null for an unknown optional field")
        # Preserve source wording/spacing instead of silently normalizing it.
        return value

    @field_validator("value_numeric", "confidence", mode="before")
    @classmethod
    def finite_decimal(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ValueError("must be a finite number or plain decimal string")
        if isinstance(value, str) and not _NUMBER.fullmatch(value):
            raise ValueError("must be a plain decimal string without commas, currency symbols, or percent signs")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("must be a valid decimal number") from exc
        if not number.is_finite():
            raise ValueError("must be finite; NaN and infinity are not permitted")
        return number

    @field_validator("period")
    @classmethod
    def validate_calendar_period(cls, value: str | None) -> str | None:
        """Check calendar-shaped periods without inventing dates for fiscal labels."""
        if value is None:
            return None
        original = value
        value = value.strip()
        try:
            if re.fullmatch(r"\d{4}", value, re.ASCII):
                date(int(value), 1, 1)
            elif re.fullmatch(r"\d{4}-\d{2}", value, re.ASCII):
                date(int(value[:4]), int(value[5:]), 1)
            elif re.fullmatch(r"\d{4}-Q[1-4]", value, re.ASCII):
                date(int(value[:4]), 1, 1)
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, re.ASCII):
                date.fromisoformat(value)
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", value, re.ASCII):
                start, end = (date.fromisoformat(part) for part in value.split("/"))
                if start > end:
                    raise ValueError("period start must not follow its end")
            elif re.match(r"^\d{4}[-/]", value, re.ASCII):
                raise ValueError("use YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Q1..Q4, or an ISO date/date range")
        except ValueError as exc:
            raise ValueError(f"invalid calendar period: {exc}") from exc
        return original


class ClaimExtractionResponse(BaseModel):
    """A required claims array; an explicit empty list means no claims found."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claims: list[Claim]
