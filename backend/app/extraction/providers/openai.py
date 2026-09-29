"""OpenAI Responses adapter using HTTPX, without implicit SDK retries."""

import httpx

from app.extraction.providers.base import ProviderError
from app.extraction.settings import ExtractionSettings
from app.llm_limits import get_llm_limiter, LLMLimitTimeout
from app.llm_retries import classify_http_error, retry_after_seconds


class OpenAIClaimProvider:
    provider_name = "openai"
    def __init__(self, settings: ExtractionSettings, *, transport=None, response_name="claim_extraction", limiter=None):
        self.settings = settings
        self.transport = transport
        self.response_name = response_name
        self.cache_namespace = f"openai:{settings.openai_model}:{response_name}"
        self.limiter = limiter if limiter is not None else get_llm_limiter()

    def generate(self, *, instructions: str, evidence: str, schema: dict) -> str:
        try:
            with self.limiter.slot(), httpx.Client(
                timeout=self.settings.extraction_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}"},
                    json={
                        "model": self.settings.openai_model,
                        "instructions": instructions,
                        "input": evidence,
                        "store": False,
                        "max_output_tokens": self.settings.extraction_max_output_tokens,
                        "text": {"format": {
                            "type": "json_schema", "name": self.response_name,
                            "strict": True, "schema": schema,
                        }},
                    },
                )
        except LLMLimitTimeout:
            raise ProviderError("queue_timeout", retryable=True) from None
        except httpx.TimeoutException:
            raise ProviderError("timeout", retryable=True) from None
        except httpx.TransportError:
            raise ProviderError("transport_error", retryable=True) from None
        if response.status_code >= 400:
            category, retryable = classify_http_error(response)
            raise ProviderError(
                category,
                retryable=retryable,
                http_status=response.status_code,
                retry_after=retry_after_seconds(response.headers.get("Retry-After")),
            )
        try:
            body = response.json()
            if body.get("status") == "incomplete":
                raise ProviderError("incomplete_output")
            if body.get("status") != "completed":
                raise ProviderError("unexpected_response_status", retryable=True)
            texts = []
            for item in body["output"]:
                if item.get("type") != "message":
                    continue
                for content in item["content"]:
                    if content.get("type") == "refusal":
                        raise ProviderError("model_refusal")
                    if content.get("type") == "output_text":
                        texts.append(content["text"])
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise ProviderError("missing_structured_output", retryable=True)
            return texts[0]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("invalid_provider_response", retryable=True) from None
