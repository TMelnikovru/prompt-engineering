"""Telegram Bot API: сообщения групп, куда добавлен бот, и личные сообщения владельцу.

Токен — секрет: он входит в адрес запросов, поэтому адреса и тексты исключений httpx
не попадают в ошибки и логи. Бот не вступает в чаты сам и пишет только владельцу.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.connectors.base import DocumentRecord

API = "https://api.telegram.org"
GROUP_TYPES = {"group", "supergroup", "channel"}


class BotError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BotApi:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if settings.telegram_bot_token is None:
            raise BotError("telegram_bot_not_configured")
        self._base = f"{API}/bot{settings.telegram_bot_token.get_secret_value()}/"
        self.client = client or httpx.AsyncClient(timeout=settings.telegram_request_timeout + 30)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        try:
            response = await self.client.post(self._base + method, json=params)
        except httpx.TimeoutException:
            raise BotError("telegram_bot_timeout") from None
        except httpx.HTTPError:
            raise BotError("telegram_bot_network_error") from None
        try:
            body = response.json()
        except ValueError:
            raise BotError("telegram_bot_invalid_response") from None
        if response.status_code == 401:
            raise BotError("telegram_bot_bad_token")
        if response.status_code == 409:
            # Другой процесс уже читает обновления этого бота (или задан webhook).
            raise BotError("telegram_bot_conflict")
        if not body.get("ok"):
            raise BotError(f"telegram_bot_http_{response.status_code}")
        return body["result"]

    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post"],
        }
        if offset is not None:
            params["offset"] = offset
        return await self.call("getUpdates", **params)

    async def send_message(self, chat_id: int | str, text: str) -> dict:
        return await self.call(
            "sendMessage", chat_id=chat_id, text=text[:4000], disable_web_page_preview=True
        )


def message_record(message: dict, fetched_at: datetime | None = None) -> DocumentRecord | None:
    """Сообщение группы или канала → документ; личные сообщения сюда не попадают."""
    chat = message.get("chat") or {}
    if chat.get("type") not in GROUP_TYPES:
        return None
    text = message.get("text") or message.get("caption") or ""
    document = message.get("document") or {}
    if document.get("file_name"):
        text += ("\n\n" if text else "") + "Файл: " + document["file_name"]
    if not text.strip():
        return None
    chat_id, message_id = str(chat["id"]), message["message_id"]
    published = datetime.fromtimestamp(message["date"], tz=UTC)
    edited = message.get("edit_date")
    username = chat.get("username")
    if username:
        url = f"https://t.me/{username}/{message_id}"
    elif chat_id.startswith("-100"):
        url = f"https://t.me/c/{chat_id[4:]}/{message_id}"
    else:
        url = "https://t.me"
    title = chat.get("title") or "Telegram"
    author = message.get("from") or {}
    return DocumentRecord(
        source_id="telegram_bot",
        external_id=f"{chat_id}:{message_id}",
        title=f"{title} · #{message_id}",
        text=text,
        url=url,
        published_at=published,
        fetched_at=fetched_at or datetime.now(UTC),
        content_type="text/plain",
        raw_content=text,
        revision_key="edit:" + str(edited or 0),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        links=[],
        attributes={
            "chat_id": chat_id,
            "chat_title": title,
            "message_id": message_id,
            "published_at": published.isoformat(),
            "edited_at": datetime.fromtimestamp(edited, tz=UTC).isoformat() if edited else None,
            "reply_to_message_id": (message.get("reply_to_message") or {}).get("message_id"),
            "author": author.get("username") or author.get("first_name"),
            "attachment": {"names": [document["file_name"]]} if document.get("file_name") else None,
        },
    )
