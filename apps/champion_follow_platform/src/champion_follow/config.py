from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    postgres_password: SecretStr | None = Field(
        default=None,
        validation_alias="POSTGRES_PASSWORD",
    )
    service_name: str = "champion-follow-core"
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
        populate_by_name=True,
    )
