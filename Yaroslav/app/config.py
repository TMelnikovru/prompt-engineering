from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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

    demo_mode: bool = False
    # Вымышленные данные для показа интерфейса; в рабочем режиме выключены.
    fake_data_enabled: bool = False
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
    dsba_base_url: str = "https://dsba.online"
    dsba_request_timeout: float = Field(default=20, ge=0.05, le=60)
    dsba_max_records: int = Field(default=1000, ge=1, le=5000)
    dsba_max_profiles: int = Field(default=40, ge=1, le=200)
    dsba_interval_seconds: int = Field(default=900, ge=60)
    # Почта ВШЭ (Яндекс 360). Пароль — пароль приложения, только в серверном .env.
    mail_imap_host: str = "imap.yandex.ru"
    mail_imap_port: int = Field(default=993, ge=1, le=65535)
    mail_imap_ssl: bool = True
    mail_username: str | None = None
    mail_password: SecretStr | None = Field(default=None, repr=False)
    mail_folders: list[str] = Field(default_factory=lambda: ["INBOX"], min_length=1, max_length=20)
    mail_lookback_days: int = Field(default=30, ge=1, le=365)
    mail_max_messages: int = Field(default=200, ge=1, le=2000)
    # Только письма с этих доменов попадают в базу и к модели; пустой список — все письма.
    mail_allowed_sender_domains: list[str] = Field(default_factory=lambda: ["hse.ru"])
    mail_web_url: str = "https://mail.yandex.ru"
    # OAuth Яндекса вместо пароля: вход через ЕЛК делает сам студент (python -m app.mail login).
    mail_oauth_client_id: str | None = None
    mail_oauth_client_secret: SecretStr | None = Field(default=None, repr=False)
    mail_oauth_token: SecretStr | None = Field(default=None, repr=False)
    mail_oauth_base_url: str = "https://oauth.yandex.ru"
    mail_request_timeout: float = Field(default=30, ge=1, le=120)
    mail_interval_seconds: int = Field(default=600, ge=60)
    mail_retry_seconds: int = Field(default=60, ge=1)
    # SmartLMS (edu.hse.ru, Moodle): ключ студента со страницы «Ключи безопасности».
    smartlms_base_url: str = "https://edu.hse.ru"
    smartlms_token: SecretStr | None = Field(default=None, repr=False)
    # Личная ссылка «Экспорт календаря» (export_execute.php?...&authtoken=...): секрет.
    smartlms_calendar_url: SecretStr | None = Field(default=None, repr=False)
    smartlms_lookback_days: int = Field(default=7, ge=0, le=90)
    smartlms_lookahead_days: int = Field(default=60, ge=1, le=365)
    smartlms_max_events: int = Field(default=500, ge=1, le=5000)
    smartlms_request_timeout: float = Field(default=30, ge=1, le=120)
    smartlms_interval_seconds: int = Field(default=900, ge=60)
    smartlms_retry_seconds: int = Field(default=60, ge=1)
    ingestion_token: SecretStr | None = Field(default=None, repr=False)
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

    @field_validator(
        "wiki_api_url",
        "openai_base_url",
        "dsba_base_url",
        "mail_web_url",
        "mail_oauth_base_url",
        "smartlms_base_url",
    )
    @classmethod
    def valid_http_endpoint(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username:
            raise ValueError("Укажите HTTP(S) URL без учётных данных")
        return value.rstrip("/")

    @model_validator(mode="after")
    def calendar_on_smartlms_host(self) -> "Settings":
        from urllib.parse import urlsplit

        if self.smartlms_calendar_url is not None:
            url = urlsplit(self.smartlms_calendar_url.get_secret_value())
            if url.scheme != "https" or url.hostname != urlsplit(self.smartlms_base_url).hostname:
                raise ValueError("Ссылка календаря должна вести на HTTPS-адрес SmartLMS")
        return self

    @field_validator("mail_allowed_sender_domains")
    @classmethod
    def normalized_domains(cls, value: list[str]) -> list[str]:
        return [item.strip().lower().lstrip("@.") for item in value if item.strip()]

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
