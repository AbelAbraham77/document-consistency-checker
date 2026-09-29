"""Provider contract; retries belong to the extraction service."""

from typing import Any, Protocol


class ProviderError(Exception):
    def __init__(self, category: str, *, retryable: bool = False, http_status=None, retry_after=None):
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after = retry_after


class ClaimProvider(Protocol):
    def generate(self, *, instructions: str, evidence: str, schema: dict[str, Any]) -> str:
        """Return raw JSON or raise a sanitized ProviderError; do not retry."""
        ...
