"""Normalization results retain input and distinguish unknowns from resolved values."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.schemas import Claim


class NormalizedUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    raw: str
    canonical: str
    dimension: str | None = None
    base_unit: str | None = None
    scale: Decimal | None = None
    recognized: bool = False


class PeriodPolicy(BaseModel):
    """Explicit fiscal assumptions; fiscal labels identify the END year."""
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    two_digit_century: Literal[1900, 2000] | None = None
    fiscal_year_end_month: int = Field(default=12, ge=1, le=12)
    fiscal_year_end_day: int = Field(default=31, ge=1, le=31)
    resolve_fiscal_dates: bool = False


class NormalizedPeriod(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    raw: str
    canonical: str
    kind: Literal["fiscal_year", "year_ended", "date", "month", "year", "unknown"]
    fiscal_year: int | None = None
    start_date: date | None = None
    end_date: date | None = None


class NormalizedClaim(BaseModel):
    """Original claim remains intact; derived values are separate for comparison."""
    model_config = ConfigDict(extra="forbid")
    original: Claim
    entity_normalized: str
    metric_normalized: str
    unit: NormalizedUnit | None
    period: NormalizedPeriod | None
    claim_id: UUID | None = None
    document_id: UUID | None = None
    chunk_id: UUID | None = None
