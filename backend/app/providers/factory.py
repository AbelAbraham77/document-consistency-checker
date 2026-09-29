"""The pipeline's single selection point for text and embedding providers."""

from app.providers.config import ProviderSettings


def text_provider(settings, *, response_name: str = "claim_extraction",
                  model: str | None = None):
    selected = ProviderSettings()
    if selected.llm_provider == "gemini":
        from app.providers.gemini import GeminiTextProvider
        return GeminiTextProvider(selected, model=model, timeout=settings.extraction_timeout_seconds,
                                  max_output_tokens=settings.extraction_max_output_tokens,
                                  response_name=response_name)
    if not settings.openai_api_key.get_secret_value().strip():
        raise ValueError("Set OPENAI_API_KEY in the backend process environment when LLM_PROVIDER=openai")
    if model:
        settings = settings.model_copy(update={"openai_model": model})
    from app.extraction.providers.openai import OpenAIClaimProvider
    return OpenAIClaimProvider(settings, response_name=response_name)


def embedding_provider(settings):
    from app.embeddings.local import LocalEmbeddingProvider
    return LocalEmbeddingProvider(settings)
