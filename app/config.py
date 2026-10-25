from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.url import postgresql_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HSE_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    demo_mode: bool = True
    timezone: str = "Europe/Moscow"
    database_url: SecretStr | None = Field(default=None, repr=False)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_connect_timeout: int = Field(default=5, ge=1, le=30)

    @field_validator("database_url")
    @classmethod
    def valid_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            postgresql_url(value.get_secret_value())
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Укажите существующий часовой пояс IANA") from exc
        return value
