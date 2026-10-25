from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.url import postgresql_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HSE_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    demo_mode: bool = True
    timezone: str = "Europe/Moscow"
    database_url: SecretStr | None = Field(default=None, repr=False)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_connect_timeout: int = Field(default=5, ge=1, le=30)
    wiki_api_url: str = "https://wiki.cs.hse.ru/api.php"
    wiki_seeds: list[str] = Field(
        default_factory=lambda: ["Заглавная страница"], min_length=1, max_length=50
    )
    wiki_max_pages: int = Field(default=25, ge=1, le=500)
    wiki_max_depth: int = Field(default=1, ge=0, le=5)
    wiki_request_timeout: float = Field(default=20, ge=0.05, le=120)
    wiki_request_delay: float = Field(default=0.25, ge=0, le=30)
    wiki_interval_seconds: int = Field(default=900, ge=60)
    wiki_retry_seconds: int = Field(default=60, ge=1)
    ingestion_token: SecretStr | None = Field(default=None, repr=False)
    telegram_api_id: int | None = Field(default=None, gt=0, repr=False)
    telegram_api_hash: SecretStr | None = Field(default=None, repr=False)
    telegram_session_path: Path = Path(".sessions/telegram.session")
    telegram_chats: list[str] = Field(default_factory=list, max_length=50)
    telegram_history_days: int = Field(default=7, ge=1, le=365)
    telegram_batch_size: int = Field(default=200, ge=1, le=2000)
    telegram_edit_scan_limit: int = Field(default=100, ge=0, le=1000)
    telegram_interval_seconds: int = Field(default=120, ge=30)
    telegram_request_timeout: float = Field(default=30, ge=0.1, le=180)
    telegram_summary_messages: int = Field(default=200, ge=1, le=1000)
    telegram_pdf_max_bytes: int = Field(default=8_000_000, ge=1000, le=20_000_000)
    telegram_pdf_max_pages: int = Field(default=30, ge=1, le=200)
    telegram_pdf_max_chars: int = Field(default=40000, ge=1000, le=200000)
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENAI_API_KEY", repr=False
    )
    openai_base_url: str = "https://api.openai.com/v1"
    extraction_model: str = Field(
        default="gpt-6-luna", validation_alias=AliasChoices("HSE_EXTRACTION_MODEL", "OPENAI_MODEL")
    )
    summary_model: str = "gpt-6.1-sol"
    ai_timeout: float = Field(default=60, ge=0.05, le=180)
    ai_chunk_chars: int = Field(default=12000, ge=1000, le=40000)
    ai_max_chunks: int = Field(default=8, ge=1, le=20)

    @field_validator("telegram_chats", mode="before")
    @classmethod
    def valid_telegram_chats(cls, value):
        if not isinstance(value, list):
            raise ValueError("Укажите JSON-массив выбранных Telegram-чатов")
        from app.connectors.telegram_refs import normalize_chat_ref

        return list(dict.fromkeys(normalize_chat_ref(item) for item in value))

    @field_validator("wiki_api_url", "openai_base_url")
    @classmethod
    def valid_http_endpoint(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username:
            raise ValueError("Укажите HTTP(S) URL без учётных данных")
        return value.rstrip("/")

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
