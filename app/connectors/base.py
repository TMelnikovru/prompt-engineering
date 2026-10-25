from typing import Protocol

from pydantic import AwareDatetime, BaseModel


class SourceRecord(BaseModel):
    """Нормализованная запись для передачи из коннектора модулю извлечения."""

    source_id: str
    external_id: str
    title: str
    text: str
    url: str | None = None
    published_at: AwareDatetime | None = None
    fetched_at: AwareDatetime
    content_type: str = "text/plain"


class Connector(Protocol):
    source_id: str

    async def fetch(self) -> list[SourceRecord]:
        """Прочитать разрешённые источники; ошибки подключения не скрывать."""
        ...
