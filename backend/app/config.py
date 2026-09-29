"""Validated environment configuration; no services are required at startup."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Document Consistency Checker", min_length=1)
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = SecretStr("")
    upload_dir: Path = BACKEND_DIR / ".uploads"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    processing_workers: int = Field(default=1, ge=1, le=4)
    llm_max_concurrency: int = Field(default=5, ge=1, le=128)
    llm_queue_timeout_seconds: float = Field(default=30, gt=0, le=600)
    document_storage: Literal["local", "supabase"] = "local"
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = "documents"
    storage_timeout_seconds: float = Field(default=60, gt=0, le=600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
