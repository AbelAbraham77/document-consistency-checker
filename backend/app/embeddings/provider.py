"""Replaceable embedding provider with bounded OpenAI HTTP retries."""

import logging
import time
import random
from typing import Protocol

import httpx

from app.database.vector_types import EMBEDDING_DIMENSIONS
from app.embeddings.settings import EmbeddingSettings
from app.embeddings.text import TEXT_VERSION
from app.embeddings.vectors import validate_vector
from app.llm_limits import get_llm_limiter, LLMLimitTimeout
from app.llm_retries import LLMServiceError, classify_http_error, retry_delay, retry_after_seconds

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    namespace: str

    def embed(self, text: str) -> list[float]: ...


class EmbeddingError(LLMServiceError):
    pass


class OpenAIEmbeddingProvider:
    provider_name = "openai"
    def __init__(self, settings: EmbeddingSettings, *, transport=None, sleep=time.sleep, limiter=None, random_value=random.random):
        self.settings, self.transport, self.sleep = settings, transport, sleep
        self.limiter = limiter if limiter is not None else get_llm_limiter()
        self.random_value = random_value
        self.namespace = f"openai:{settings.embedding_model}:{EMBEDDING_DIMENSIONS}:{TEXT_VERSION}"

    def embed(self, text: str) -> list[float]:
        key = self.settings.openai_api_key.get_secret_value().strip()
        if not key:
            raise EmbeddingError("Set OPENAI_API_KEY in the process environment", category="configuration", attempts=0)
        for attempt in range(self.settings.embedding_max_retries + 1):
            retryable = True
            http_status, retry_after = None, None
            try:
                with self.limiter.slot(), httpx.Client(timeout=self.settings.embedding_timeout_seconds, transport=self.transport) as client:
                    response = client.post("https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": self.settings.embedding_model, "input": text,
                              "encoding_format": "float", "dimensions": EMBEDDING_DIMENSIONS})
                if response.status_code >= 400:
                    category, retryable = classify_http_error(response)
                    http_status = response.status_code
                    retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                else:
                    body = response.json()
                    if len(body["data"]) != 1 or body["data"][0]["index"] != 0:
                        raise ValueError("Unexpected embedding response")
                    return validate_vector(body["data"][0]["embedding"])
            except LLMLimitTimeout:
                category = "queue_timeout"
            except httpx.TimeoutException:
                category = "timeout"
            except httpx.TransportError:
                category = "transport_error"
            except (ValueError, KeyError, TypeError, OverflowError):
                category = "invalid_response"
            logger.warning("Embedding attempt failed: attempt=%d category=%s", attempt + 1, category)
            if not retryable or attempt == self.settings.embedding_max_retries:
                raise EmbeddingError(f"Embedding failed: {category}", category=category, retryable=retryable,
                                     attempts=attempt+1, http_status=http_status, provider=self.provider_name) from None
            self.sleep(retry_delay(attempt, retry_after=retry_after, random_value=self.random_value))
        raise AssertionError("unreachable")
