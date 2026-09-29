"""Embedding settings; API credentials come only from the process environment."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)
    openai_api_key: SecretStr = SecretStr("")
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1, max_length=150)
    embedding_similarity_threshold: float = Field(default=0.80, ge=-1, le=1)
    embedding_timeout_seconds: float = Field(default=60, gt=0, le=600)
    embedding_max_retries: int = Field(default=2, ge=0, le=10)
    embedding_cache_path: Path = Path(__file__).resolve().parents[2] / ".cache" / "embeddings.sqlite3"
