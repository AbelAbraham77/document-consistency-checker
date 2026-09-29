"""Entity/metric blocking and deterministic context-aware candidate generation."""

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal, localcontext
from itertools import combinations
import re

from app.matching.models import CandidatePair
from app.normalization.models import NormalizedClaim, NormalizedPeriod


def _label(value: str | None) -> str | None:
    # Do not remove punctuation, negation or qualifiers from comparison context.
    return " ".join(value.lower().split()) if value and value.strip() else None


def _period_relation(a: NormalizedPeriod | None, b: NormalizedPeriod | None) -> str:
    if a is None or b is None or "unknown" in (a.kind, b.kind):
        return "unknown"
    if any(period.kind == "fiscal_year" and period.fiscal_year is None for period in (a, b)):
        return "unknown"
    if a.kind == b.kind and a.canonical == b.canonical:
        if a.start_date != b.start_date or a.end_date != b.end_date:
            return "different_basis"
        return "same"
    if a.kind == b.kind:
        return "temporal"
    if a.start_date and a.end_date and b.start_date and b.end_date:
        if a.end_date < b.start_date or b.end_date < a.start_date:
            return "temporal"
    # A date, month, fiscal label and annual reporting window are not interchangeable.
    return "different_basis"


def _unit_relation(a: NormalizedClaim, b: NormalizedClaim) -> tuple[str, Decimal, Decimal]:
    left, right = a.unit, b.unit
    if left is None or right is None:
        return "unknown", Decimal(1), Decimal(1)
    if left.recognized and right.recognized:
        if (left.dimension, left.base_unit) == (right.dimension, right.base_unit):
            if left.scale is not None and right.scale is not None:
                return "same", left.scale, right.scale
    elif not left.recognized and not right.recognized and left.canonical == right.canonical:
        # Identical explicit labels (e.g. Cr) are comparable without guessing currency.
        return "same", Decimal(1), Decimal(1)
    return "different", Decimal(1), Decimal(1)


def _scaled(value: Decimal, scale: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + len(scale.as_tuple().digits))
        return value * scale


def _compare_pair(a: NormalizedClaim, b: NormalizedClaim) -> CandidatePair | None:
    unit, scale_a, scale_b = _unit_relation(a, b)
    numeric_a, numeric_b = a.original.value_numeric, b.original.value_numeric
    numeric = numeric_a is not None and numeric_b is not None
    if numeric and unit == "same":
        if _scaled(numeric_a, scale_a) == _scaled(numeric_b, scale_b):
            return None
    elif not numeric and numeric_a is None and numeric_b is None:
        if _label(a.original.value_text) == _label(b.original.value_text):
            return None
    elif numeric and unit == "unknown" and numeric_a == numeric_b:
        return None

    period = _period_relation(a.period, b.period)
    scope_a, scope_b = _label(a.original.scope), _label(b.original.scope)
    qualifier_a, qualifier_b = _label(a.original.qualifier), _label(b.original.qualifier)
    exact_basis = {"actual", "standalone", "consolidated", "audited"}
    qualified = qualifier_a != qualifier_b or (
        qualifier_a is not None and not set(re.split(r"\s*[;,]\s*", qualifier_a)).issubset(exact_basis))
    reasons = []
    if period == "temporal":
        classification = "temporal_difference"
        reasons.append("Different reporting periods; a value change across time is not itself a contradiction.")
    elif scope_a is not None and scope_b is not None and scope_a != scope_b:
        classification = "possible_scope_difference"
    elif qualified:
        # Even equal 'approximately'/'at least' qualifiers require interval/rounding reasoning.
        classification = "possible_qualifier_difference"
    elif unit == "different":
        classification = "possible_unit_difference"
    elif period == "different_basis":
        classification = "possible_period_difference"
    elif period == "unknown" or scope_a is None or scope_b is None or unit == "unknown":
        classification = "insufficient_context"
    elif not numeric:
        classification = "possible_value_difference"
    else:
        classification = "candidate_contradiction"

    if scope_a != scope_b:
        reasons.append("Scope differs or is missing; do not assume the populations match.")
    if qualified:
        reasons.append("Qualifiers need review for reporting basis, bounds, approximation or conditions.")
    if unit != "same":
        reasons.append("Units are incompatible or missing; values cannot be directly compared.")
    elif numeric:
        reasons.append("Numeric values differ after exact conversion to the same unit scale.")
    else:
        reasons.append("Textual or mixed values differ; semantic verification is needed.")
    if period in ("unknown", "different_basis"):
        reasons.append("Reporting periods are missing, ambiguous or expressed on different bases.")
    if scope_a is None and scope_b is None:
        reasons.append("Neither claim states its scope.")
    if classification == "candidate_contradiction":
        reasons.append("Entity, metric, period, scope and unit match; qualifiers do not indicate uncertainty or a basis difference.")
    return CandidatePair(claim_a=a.model_copy(deep=True), claim_b=b.model_copy(deep=True),
                         reason_for_candidate=" ".join(reasons), preliminary_classification=classification)


def generate_structured_candidates(claims: Iterable[NormalizedClaim]) -> list[CandidatePair]:
    """Generate unique pairs within exact entity/metric buckets for ONE document.

    No cross-bucket comparisons, DB access, embeddings or LLM calls. Identical
    full input records are deduplicated; distinct source occurrences remain.
    """
    buckets: dict[tuple[str, str], list[NormalizedClaim]] = defaultdict(list)
    seen = set()
    document_ids = set()
    for claim in claims:
        if not isinstance(claim, NormalizedClaim):
            raise TypeError("Expected NormalizedClaim objects from one document")
        if claim.document_id is not None:
            document_ids.add(claim.document_id)
            if len(document_ids) > 1:
                raise ValueError("Candidate generation requires claims from one document")
        if not claim.entity_normalized.strip() or not claim.metric_normalized.strip():
            raise ValueError("Normalized entity and metric must not be blank")
        identity = claim.model_dump_json()
        if identity in seen:
            continue
        seen.add(identity)
        buckets[(claim.entity_normalized, claim.metric_normalized)].append(claim)
    result = []
    for bucket in buckets.values():
        for a, b in combinations(bucket, 2):
            candidate = _compare_pair(a, b)
            if candidate is not None:
                result.append(candidate)
    return result
