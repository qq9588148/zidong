from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHAMPION_",
        case_sensitive=False,
        extra="forbid",
    )

    database_url: str
    public_base_url: AnyHttpUrl
    trusted_admin_origin: AnyHttpUrl
    task_signing_key_path: Path
    task_signing_key_version: str = Field(
        default="task-v1", pattern=r"^[a-z0-9-]{1,32}$"
    )
    secret_vault_key_path: Path
    allocation_seed_path: Path
    allocation_seed_version: str = Field(
        default="allocation-v1", pattern=r"^[a-z0-9-]{1,32}$"
    )
    token_pepper: SecretStr = Field(min_length=32)
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=86_400)
    enrollment_challenge_ttl_seconds: int = Field(default=300, ge=60, le=900)
    authorization_code_ttl_seconds: int = Field(default=86_400, ge=300)
    threshold_preview_ttl_seconds: int = Field(default=1800, ge=300, le=3600)

    @field_validator("public_base_url", "trusted_admin_origin")
    @classmethod
    def require_https_origin(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("public origins must use https")
        return value
