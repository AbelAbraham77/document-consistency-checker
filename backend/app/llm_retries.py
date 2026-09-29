"""Provider-independent retry delays and sanitized service failures."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random


PERMANENT_429_CODES = frozenset({
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "gemini_daily_quota",
})


def error_detail(category: str) -> str:
    """Explain common provider failures without echoing sensitive response bodies."""
    if category == "http_429":
        return "http_429 (provider rate limit or quota; check account limits and retry)"
    if category == "gemini_daily_quota":
        return "gemini_daily_quota (the Gemini project reached its daily quota; check AI Studio limits)"
    if category in {"http_401", "http_403"}:
        return f"{category} (check the selected provider API key and access)"
    if category in {"missing_gemini_api_key", "missing_openai_api_key"}:
        return f"{category} (set the selected provider API key in the backend environment)"
    if category == "invalid_gemini_api_key":
        return "invalid_gemini_api_key (check GEMINI_API_KEY in the backend terminal)"
    if category == "invalid_gemini_schema":
        return "invalid_gemini_schema (Gemini rejected the structured-output schema)"
    if category == "invalid_gemini_model":
        return "invalid_gemini_model (check GEMINI_MODEL access for this API key)"
    if category == "invalid_gemini_request":
        return "invalid_gemini_request (Gemini rejected the request; check model access and configuration)"
    if category == "invalid_claim_output":
        return "invalid_claim_output (generated claims did not match the required format or source text)"
    return category


def classify_http_error(response) -> tuple[str, bool]:
    """Use only known API error codes; never expose provider response text."""
    status = response.status_code
    if status == 429:
        try:
            error = response.json().get("error", {})
            code = error.get("code") or error.get("type")
        except (ValueError, TypeError, AttributeError):
            code = None
        if isinstance(code, str) and code in PERMANENT_429_CODES:
            return code, False
        if isinstance(code, str) and code in {"slow_down", "rate_limit_exceeded"}:
            return code, True
    return f"http_{status}", status in (408, 429) or status >= 500


class LLMServiceError(RuntimeError):
    def __init__(self, message: str, *, category="unknown", retryable=False, attempts=1,
                 http_status=None, provider=None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.attempts = attempts
        self.http_status = http_status
        self.provider = provider


def retry_after_seconds(value: str | None) -> float | None:
    """Accept Retry-After seconds or HTTP date; bound untrusted provider headers."""
    if not value:
        return None
    try:
        try:
            delay = float(value)
        except ValueError:
            date = parsedate_to_datetime(value)
            delay = (date - datetime.now(timezone.utc)).total_seconds()
        return min(120.0, max(0.0, delay)) if math.isfinite(delay) else None
    except (ValueError, TypeError, OverflowError):
        return None


def retry_delay(attempt: int, *, base=1.0, cap=30.0, retry_after=None, random_value=random.random) -> float:
    """Equal jitter spreads retries across half to all of an exponential window."""
    window = min(cap, base * 2 ** attempt)
    jittered = window * (0.5 + 0.5 * random_value())
    return max(jittered, min(120.0, max(0.0, retry_after or 0.0)))
