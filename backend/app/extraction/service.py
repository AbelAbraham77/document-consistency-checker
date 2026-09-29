"""Synchronous extraction orchestration, bounded retries and local validation."""

import json
import logging
import time
import random
from collections.abc import Callable
from app.extraction.cache import (
    ExtractionCache, MemoryExtractionCache, SQLiteExtractionCache,
    cache_entry, content_hash, reuse_entry,
)

from app.extraction.grounding import evidence_segments, validate_grounding
from app.extraction.prompts import INSTRUCTIONS, response_schema
from app.extraction.providers.base import ClaimProvider, ProviderError
from app.providers.factory import text_provider
from app.extraction.schemas import Claim, ClaimExtractionResponse
from app.extraction.settings import ExtractionSettings
from app.extraction.validation import validate_claim_extraction_response
from app.parsing.models import DocumentChunk
from app.llm_retries import LLMServiceError, retry_delay, error_detail

logger = logging.getLogger(__name__)


class ExtractionError(LLMServiceError):
    """Sanitized extraction failure; no model text or credentials in the message."""


class ClaimExtractionService:
    def __init__(self, provider: ClaimProvider, *, max_retries: int = 2,
                 backoff_seconds: float = 1, backoff_cap_seconds: float = 30,
                 sleep: Callable[[float], None] = time.sleep,
                 cache: ExtractionCache | None = None, random_value=random.random,
                 cache_namespace: str | None = None):
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        if not 0 < backoff_seconds <= 60 or not 0 < backoff_cap_seconds <= 120:
            raise ValueError("backoff delays must be positive and bounded")
        self.provider = provider
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        self.sleep = sleep
        self.random_value = random_value
        self.cache = cache if cache is not None else MemoryExtractionCache()
        self.cache_namespace = cache_namespace or getattr(provider, "cache_namespace",
            f"{type(provider).__module__}.{type(provider).__qualname__}")

    def extract(self, chunk: DocumentChunk) -> ClaimExtractionResponse:
        # Section affects interpretation; model/prompt changes must invalidate old output.
        # Absolute pages are deliberately excluded: reuse_entry rebinds provenance.
        key = content_hash(json.dumps({"text": content_hash(chunk.text),
            "section": chunk.section, "provider": self.cache_namespace,
            "instructions": INSTRUCTIONS, "schema": response_schema()}, sort_keys=True))
        with self.cache.locked(key) as slot:
            cached = slot.get()
            if cached is not None:
                response = reuse_entry(cached, chunk)
                logger.info("Claim extraction cache hit: claims=%d", len(response.claims))
                return response
            logger.info("Claim extraction cache miss")
            response = self._extract_uncached(chunk)
            slot.put(cache_entry(chunk, response))
            return response

    def _extract_uncached(self, chunk: DocumentChunk) -> ClaimExtractionResponse:
        segments = evidence_segments(chunk)
        evidence = json.dumps({"section": chunk.section, "segments": segments}, ensure_ascii=False)
        instructions = INSTRUCTIONS
        for attempt in range(self.max_retries + 1):
            http_status, retry_after = None, None
            try:
                raw = self.provider.generate(instructions=instructions, evidence=evidence, schema=response_schema())
                response = validate_claim_extraction_response(raw)
                validate_grounding(response, segments)
                logger.info("Claim extraction succeeded: attempts=%d claims=%d", attempt + 1, len(response.claims))
                return response
            except ProviderError as exc:
                category, retryable = exc.category, exc.retryable
                http_status, retry_after = exc.http_status, exc.retry_after
            except ValueError:
                category, retryable = "invalid_claim_output", True
                # Repeating the same prompt often repeats the same invalid table quote.
                # Keep the correction generic; provider output may contain private text.
                instructions = INSTRUCTIONS + (
                    "\nThe previous response failed local validation. Recheck every claim against "
                    "the evidence: copy claim_text as one exact contiguous quote from a "
                    "segment with a known page, copy value_text verbatim from that quote, "
                    "and use a plain decimal value_numeric only when it matches the "
                    "quoted number. Omit any claim that cannot meet these checks.\n"
                )
            logger.warning("Claim extraction failed: attempt=%d category=%s", attempt + 1, category)
            if not retryable or attempt == self.max_retries:
                raise ExtractionError(f"Claim extraction failed after {attempt + 1} attempt(s): {error_detail(category)}",
                    category=category, retryable=retryable, attempts=attempt+1, http_status=http_status,
                    provider=getattr(self.provider, "provider_name", None)) from None
            delay = retry_delay(attempt, base=self.backoff_seconds, cap=self.backoff_cap_seconds,
                                retry_after=retry_after, random_value=self.random_value)
            logger.info("Retrying claim extraction in %.2f seconds", delay)
            self.sleep(delay)
        raise AssertionError("unreachable")


def default_service() -> ClaimExtractionService:
    settings = ExtractionSettings()
    return ClaimExtractionService(
        text_provider(settings), max_retries=settings.extraction_max_retries,
        backoff_seconds=settings.extraction_backoff_seconds,
        backoff_cap_seconds=settings.extraction_backoff_cap_seconds,
        cache=SQLiteExtractionCache(settings.extraction_cache_path),
    )


def extract_claims(chunk: DocumentChunk) -> list[Claim]:
    """Extract a single chunk using process environment settings."""
    return default_service().extract(chunk).claims
