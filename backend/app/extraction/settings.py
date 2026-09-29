"""Extraction settings read exclusively from process environment variables."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class ExtractionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = Field(default="gpt-4o-mini", min_length=1)
    extraction_timeout_seconds: float = Field(default=60, gt=0, le=600)
    extraction_max_retries: int = Field(default=2, ge=0, le=10)
    extraction_backoff_seconds: float = Field(default=1, gt=0, le=60)
    extraction_backoff_cap_seconds: float = Field(default=30, gt=0, le=120)
    extraction_max_output_tokens: int = Field(default=8192, ge=256)
    extraction_cache_path: Path = Path(__file__).resolve().parents[2] / ".cache" / "claims.sqlite3"

    @field_validator("openai_model")
    @classmethod
    def nonblank(cls, value):
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not text.strip():
            raise ValueError("must not be blank")
        return value
