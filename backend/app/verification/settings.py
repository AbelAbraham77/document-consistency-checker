"""Environment-only verifier credentials and bounded request settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VerificationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)
    openai_api_key: SecretStr = SecretStr("")
    model: str = Field(default="", validation_alias="VERIFICATION_MODEL")
    verification_timeout_seconds: float = Field(default=60, gt=0, le=600)
    verification_max_retries: int = Field(default=2, ge=0, le=10)
    verification_max_output_tokens: int = Field(default=8192, ge=256)
    verification_max_context_characters: int = Field(default=120000, ge=1000, le=1000000)
