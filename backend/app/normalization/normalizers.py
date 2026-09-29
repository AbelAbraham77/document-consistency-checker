"""Deterministic normalization; no model calls, entity resolution or value conversion."""

import calendar
from datetime import date, timedelta
from decimal import Decimal
import re
from collections.abc import Mapping

from app.extraction.schemas import Claim
from app.normalization.models import NormalizedClaim, NormalizedPeriod, NormalizedUnit, PeriodPolicy
from app.normalization.registry import DEFAULT_REGISTRY, MetricRegistry
from app.normalization.text import clean_text


def normalize_entity(value: str, *, aliases: Mapping[str, str] | None = None) -> str:
    text = clean_text(value)
    text = re.sub(r"^(?:the|this)\s+", "", text)
    # Expand legal suffix abbreviations, but never drop them or merge subsidiaries.
    text = re.sub(r"\bpvt\.?\b\.?", "private", text)
    text = re.sub(r"\bltd\.?\b\.?", "limited", text)
    text = re.sub(r"\binc\.?\b\.?", "incorporated", text)
    if not text:
        raise ValueError("Entity must not be blank")
    if aliases:
        resolved = {}
        for alias, canonical in aliases.items():
            key = normalize_entity(alias)
            target = normalize_entity(canonical)
            if key in resolved and resolved[key] != target:
                raise ValueError("Conflicting entity aliases")
            resolved[key] = target
        return resolved.get(text, text)
    return text


def normalize_metric(value: str, *, registry: MetricRegistry = DEFAULT_REGISTRY) -> str:
    text = clean_text(value)
    if not text:
        raise ValueError("Metric must not be blank")
    return registry.lookup(text) or text.replace(" ", "_")


def normalize_unit(value: str | None) -> NormalizedUnit | None:
    if value is None:
        return None
    text = clean_text(value)
    if not text:
        raise ValueError("Use None for an unknown unit")
    scales = {"": ("", "1"), "crore": ("crore", "10000000"), "crores": ("crore", "10000000"),
              "cr": ("crore", "10000000"), "million": ("million", "1000000"),
              "millions": ("million", "1000000"), "mn": ("million", "1000000"),
              "lakh": ("lakh", "100000"), "lakhs": ("lakh", "100000"),
              "thousand": ("thousand", "1000"), "billion": ("billion", "1000000000")}
    currency = re.fullmatch(r"(inr|₹|indian rupees?|usd|us\$|eur|€|gbp|£)\s*(.*)", text)
    if currency and currency[2].rstrip(".") in scales:
        currency_name = currency[1]
        base = ("INR" if currency_name in ("inr", "₹") or currency_name.startswith("indian rupee")
                else "USD" if currency_name in ("usd", "us$")
                else "EUR" if currency_name in ("eur", "€") else "GBP")
        suffix, scale = scales[currency[2].rstrip(".")]
        return NormalizedUnit(raw=value, canonical=base + (" " + suffix if suffix else ""),
                              dimension="currency", base_unit=base, scale=Decimal(scale), recognized=True)
    groups = [
        (("%", "percent", "percentage", "per cent", "pct"), "percent", "ratio", "ratio", "0.01"),
        (("percentage point", "percentage points", "pp"), "percentage_point", "percentage_point", "percentage_point", "1"),
        (("basis point", "basis points", "bps"), "basis_point", "percentage_point", "percentage_point", "0.01"),
        (("employee", "employees"), "employees", "count", "employees", "1"),
        (("person", "people", "persons"), "persons", "count", "persons", "1"),
        (("share", "shares"), "shares", "count", "shares", "1"),
    ]
    for aliases, canonical, dimension, base, scale in groups:
        if text in aliases:
            return NormalizedUnit(raw=value, canonical=canonical, dimension=dimension,
                                  base_unit=base, scale=Decimal(scale), recognized=True)
    # Bare Rs/$ are ambiguous across currencies; never silently assume INR/USD.
    return NormalizedUnit(raw=value, canonical=text)


def _date(text: str) -> date | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    months = {name.lower(): index for index in range(1, 13)
              for name in (calendar.month_name[index], calendar.month_abbr[index])}
    match = re.fullmatch(r"([a-z]+)\.? (\d{1,2}),? (\d{4})", text)
    reverse = re.fullmatch(r"(\d{1,2}) ([a-z]+)\.? (\d{4})", text)
    if match and match[1] in months:
        return date(int(match[3]), months[match[1]], int(match[2]))
    if reverse and reverse[2] in months:
        return date(int(reverse[3]), months[reverse[2]], int(reverse[1]))
    return None


def normalize_period(value: str | None, *, policy: PeriodPolicy | None = None) -> NormalizedPeriod | None:
    if value is None:
        return None
    # Date hyphens are significant; do not use metric punctuation cleanup here.
    text = re.sub(r"\s+", " ", value.strip().lower())
    if not text:
        raise ValueError("Use None for an unknown period")
    policy = policy or PeriodPolicy()
    fiscal = re.fullmatch(r"(?:fy|fiscal year|financial year)\s*'?([0-9]{2}|[0-9]{4})", text)
    if fiscal:
        digits = fiscal[1]
        year = int(digits) if len(digits) == 4 else (
            policy.two_digit_century + int(digits) if policy.two_digit_century is not None else None)
        start = end = None
        if year is not None:
            date(year, 1, 1)
        if year is not None and policy.resolve_fiscal_dates:
            end = date(year, policy.fiscal_year_end_month, policy.fiscal_year_end_day)
            start = date(year - 1, policy.fiscal_year_end_month, policy.fiscal_year_end_day) + timedelta(days=1)
        return NormalizedPeriod(raw=value, canonical=f"FY{year if year is not None else digits}",
                                kind="fiscal_year", fiscal_year=year, start_date=start, end_date=end)
    ended = re.fullmatch(r"year ended (.+)", text)
    if ended:
        end = _date(ended[1])
        if end:
            # Keep duration type, but do not guess an unstated start (52/53-week calendars).
            return NormalizedPeriod(raw=value, canonical=f"year_ended:{end.isoformat()}",
                                    kind="year_ended", end_date=end)
    exact = _date(text)
    if exact:
        return NormalizedPeriod(raw=value, canonical=exact.isoformat(), kind="date", end_date=exact)
    month_names = {name.lower(): index for index in range(1, 13)
                   for name in (calendar.month_name[index], calendar.month_abbr[index])}
    named_month = re.fullmatch(r"([a-z]+)\.? (\d{4})", text)
    iso_month = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if iso_month or (named_month and named_month[1] in month_names):
        year, month = ((int(iso_month[1]), int(iso_month[2])) if iso_month else
                       (int(named_month[2]), month_names[named_month[1]]))
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return NormalizedPeriod(raw=value, canonical=f"{year:04d}-{month:02d}", kind="month",
                                start_date=start, end_date=end)
    if re.fullmatch(r"\d{4}", text):
        date(int(text), 1, 1)
        return NormalizedPeriod(raw=value, canonical=text, kind="year")
    return NormalizedPeriod(raw=value, canonical=text, kind="unknown")


def normalize_claim(claim: Claim, *, registry: MetricRegistry = DEFAULT_REGISTRY,
                    entity_aliases: Mapping[str, str] | None = None,
                    period_policy: PeriodPolicy | None = None) -> NormalizedClaim:
    return NormalizedClaim(
        original=claim.model_copy(deep=True),
        entity_normalized=normalize_entity(claim.entity_raw, aliases=entity_aliases),
        metric_normalized=normalize_metric(claim.metric_raw, registry=registry),
        unit=normalize_unit(claim.unit), period=normalize_period(claim.period, policy=period_policy),
    )


def normalize_stored_claim(claim, **kwargs) -> NormalizedClaim:
    """Normalize a persisted ORM claim while carrying its source identities."""
    from app.models import Claim as StoredClaim
    if not isinstance(claim, StoredClaim) or any(value is None for value in (claim.id, claim.document_id, claim.chunk_id)):
        raise ValueError("Expected a persisted claim with document and chunk IDs")
    original = Claim.model_validate({name: getattr(claim, name) for name in Claim.model_fields})
    result = normalize_claim(original, **kwargs)
    result.claim_id, result.document_id, result.chunk_id = claim.id, claim.document_id, claim.chunk_id
    return result
