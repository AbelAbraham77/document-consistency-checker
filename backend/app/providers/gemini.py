"""Native Gemini generateContent and embedContent HTTP adapters."""

import random
import time
import json
import re

import httpx

from app.database.vector_types import EMBEDDING_DIMENSIONS
from app.embeddings.provider import EmbeddingError
from app.embeddings.settings import EmbeddingSettings
from app.embeddings.text import TEXT_VERSION
from app.embeddings.vectors import validate_vector
from app.extraction.providers.base import ProviderError
from app.llm_limits import LLMLimitTimeout, get_llm_limiter
from app.llm_retries import classify_http_error, retry_after_seconds, retry_delay, error_detail
from app.providers.config import ProviderSettings


BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def gemini_retry_after(response) -> float | None:
    """Honor Google's RetryInfo hint, falling back to bounded HTTP retry delays."""
    header_delay = retry_after_seconds(response.headers.get("Retry-After"))
    try:
        details = response.json().get("error", {}).get("details", [])
        for detail in details:
            if isinstance(detail, dict) and detail.get("@type", "").endswith("google.rpc.RetryInfo"):
                match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
                if match:
                    return max(header_delay or 0.0, min(120.0, float(match.group(1))))
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    return header_delay


def classify_gemini_error(response) -> tuple[str, bool]:
    """Classify known Google errors without showing its potentially sensitive message."""
    if response.status_code == 429:
        try:
            error = response.json().get("error", {})
            if error.get("code") == "quota_exceeded":
                return "gemini_daily_quota", False
            for detail in error.get("details", []):
                if not isinstance(detail, dict) or not detail.get("@type", "").endswith("google.rpc.QuotaFailure"):
                    continue
                for violation in detail.get("violations", []):
                    quota_id = str(violation.get("quotaId", "")).lower()
                    if "perday" in quota_id or "per_day" in quota_id or "daily" in quota_id:
                        return "gemini_daily_quota", False
        except (ValueError, TypeError, AttributeError):
            pass
        return "http_429", True
    if response.status_code != 400:
        return classify_http_error(response)
    try:
        error = response.json().get("error", {})
        reasons = {detail.get("reason") for detail in error.get("details", [])
                   if isinstance(detail, dict)}
        if reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_REVOKED", "API_KEY_BLOCKED"}:
            return "invalid_gemini_api_key", False
        message = error.get("message", "").lower()
        if any(term in message for term in ("api key not valid", "api key was reported as leaked",
                                            "api key expired", "api key is invalid")):
            return "invalid_gemini_api_key", False
        if any(term in message for term in ("schema", "responseformat", "response_format")):
            return "invalid_gemini_schema", False
        if "model" in message:
            return "invalid_gemini_model", False
    except (ValueError, TypeError, AttributeError):
        pass
    return "invalid_gemini_request", False


def gemini_schema(schema: dict) -> dict:
    """Reduce Pydantic JSON Schema to Gemini's documented structured-output subset."""
    definitions = schema.get("$defs", {})

    def convert(node):
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/$defs/")
            if name not in definitions:
                raise ValueError("Unsupported provider schema reference")
            return convert(definitions[name])
        if "anyOf" in node:
            choices = [convert(choice) for choice in node["anyOf"]]
            if all("type" in choice for choice in choices):
                types = [choice["type"] for choice in choices]
                # Gemini documents nullable single types. It does not promise
                # arbitrary multi-type unions such as number|string|null.
                if "number" in types and "string" in types:
                    types.remove("string")
                return {"type": types}
            raise ValueError("Unsupported provider schema union")
        allowed = {"type", "title", "description", "properties", "required",
                   "additionalProperties", "items", "enum", "minimum", "maximum",
                   "minItems", "maxItems", "format"}
        return {key: ({name: convert(child) for name, child in value.items()}
                      if key == "properties" else convert(value))
                for key, value in node.items() if key in allowed}

    return convert(schema)


class GeminiTextProvider:
    provider_name = "gemini"
    def __init__(self, settings: ProviderSettings, *, model: str | None = None,
                 timeout: float = 60, max_output_tokens: int = 8192,
                 response_name: str = "claim_extraction", transport=None, limiter=None):
        self.settings = settings
        self.model = model or settings.gemini_model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.response_name = response_name
        self.transport = transport
        self.limiter = limiter if limiter is not None else get_llm_limiter()
        self.cache_namespace = f"gemini:{self.model}:{response_name}"
        self._format_mode = 0
        self.before_request = None

    def generate(self, *, instructions: str, evidence: str, schema: dict) -> str:
        key = self.settings.gemini_api_key.get_secret_value().strip()
        if not key:
            raise ProviderError("missing_gemini_api_key")
        portable_schema = gemini_schema(schema)
        for mode in range(self._format_mode, 3):
            generation = {"maxOutputTokens": self.max_output_tokens}
            system_text = instructions
            if mode == 0:
                generation["responseFormat"] = {"text": {"mimeType": "application/json",
                                                          "schema": portable_schema}}
            elif mode == 1:
                generation.update(responseMimeType="application/json", responseJsonSchema=portable_schema)
            else:
                generation["responseMimeType"] = "application/json"
                system_text += "\nReturn only a JSON object matching this schema: " + json.dumps(
                    portable_schema, separators=(",", ":"))
            try:
                with self.limiter.slot(), httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                    if self.before_request:
                        self.before_request()
                    response = client.post(
                        f"{BASE_URL}/{self.model}:generateContent",
                        headers={"x-goog-api-key": key},
                        json={
                            "systemInstruction": {"parts": [{"text": system_text}]},
                            "contents": [{"role": "user", "parts": [{"text": evidence}]}],
                            "generationConfig": generation,
                        },
                    )
            except LLMLimitTimeout:
                raise ProviderError("queue_timeout", retryable=True) from None
            except httpx.TimeoutException:
                raise ProviderError("timeout", retryable=True) from None
            except httpx.TransportError:
                raise ProviderError("transport_error", retryable=True) from None
            if response.status_code >= 400:
                category, retryable = classify_gemini_error(response)
                if response.status_code == 400 and category == "invalid_gemini_schema" and mode < 2:
                    continue
                raise ProviderError(category, retryable=retryable, http_status=response.status_code,
                                    retry_after=gemini_retry_after(response))
            self._format_mode = mode
            break
        try:
            body = response.json()
            candidates = body["candidates"]
            if len(candidates) != 1:
                raise ProviderError("missing_structured_output", retryable=True)
            candidate = candidates[0]
            if candidate.get("finishReason") != "STOP":
                raise ProviderError("incomplete_output" if candidate.get("finishReason") == "MAX_TOKENS"
                                    else "blocked_or_incomplete_output")
            parts = candidate["content"]["parts"]
            texts = [part["text"] for part in parts if "text" in part]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise ProviderError("missing_structured_output", retryable=True)
            return texts[0]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("invalid_provider_response", retryable=True) from None


class GeminiEmbeddingProvider:
    provider_name = "gemini"
    def __init__(self, provider_settings: ProviderSettings, settings: EmbeddingSettings, *,
                 transport=None, sleep=time.sleep, limiter=None, random_value=random.random):
        self.provider_settings = provider_settings
        self.settings = settings
        self.transport, self.sleep = transport, sleep
        self.limiter = limiter if limiter is not None else get_llm_limiter()
        self.random_value = random_value
        self.model = provider_settings.gemini_embedding_model
        self.namespace = f"gemini:{self.model}:{EMBEDDING_DIMENSIONS}:{TEXT_VERSION}"

    def embed(self, text: str) -> list[float]:
        key = self.provider_settings.gemini_api_key.get_secret_value().strip()
        if not key:
            raise EmbeddingError("Set GEMINI_API_KEY in the backend process environment",
                                 category="configuration", attempts=0)
        for attempt in range(self.settings.embedding_max_retries + 1):
            retryable, http_status, retry_after = True, None, None
            try:
                with self.limiter.slot(), httpx.Client(timeout=self.settings.embedding_timeout_seconds,
                                                      transport=self.transport) as client:
                    response = client.post(f"{BASE_URL}/{self.model}:embedContent",
                        headers={"x-goog-api-key": key},
                        json={"model": f"models/{self.model}", "content": {"parts": [{"text": text}]},
                              "embedContentConfig": {"outputDimensionality": EMBEDDING_DIMENSIONS,
                                                     "autoTruncate": False}})
                if response.status_code >= 400:
                    category, retryable = classify_gemini_error(response)
                    http_status = response.status_code
                    retry_after = gemini_retry_after(response)
                else:
                    return validate_vector(response.json()["embedding"]["values"])
            except LLMLimitTimeout:
                category = "queue_timeout"
            except httpx.TimeoutException:
                category = "timeout"
            except httpx.TransportError:
                category = "transport_error"
            except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
                category = "invalid_response"
            if not retryable or attempt == self.settings.embedding_max_retries:
                raise EmbeddingError(f"Embedding failed after {attempt + 1} attempt(s): {error_detail(category)}",
                                     category=category, retryable=retryable,
                                     attempts=attempt + 1, http_status=http_status, provider=self.provider_name) from None
            self.sleep(retry_delay(attempt, retry_after=retry_after, random_value=self.random_value))
        raise AssertionError("unreachable")
