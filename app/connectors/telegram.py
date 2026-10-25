"""Read-only MTProto connector. No implicit login, joining, or message sending."""

import asyncio
import fcntl
import hashlib
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pydantic import Field
from pypdf import PdfReader
from telethon import TelegramClient, errors, utils
from telethon.tl import types

from app.config import Settings
from app.connectors.base import SourceRecord

# Third-party parser diagnostics may contain fragments of the source document.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)


class TelegramError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TelegramMessage(SourceRecord):
    source_id: str = "telegram"
    attributes: dict = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        content = {"text": self.text, "metadata": self.attributes}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def telegram_error(exc: Exception) -> TelegramError:
    if isinstance(exc, TelegramError):
        return exc
    if isinstance(exc, errors.FloodWaitError):
        return TelegramError(f"telegram_flood_wait_{exc.seconds}")
    if isinstance(exc, (errors.UnauthorizedError, errors.AuthKeyError)):
        return TelegramError("telegram_authorization_required")
    if isinstance(exc, errors.RPCError):
        return TelegramError("telegram_access_or_rpc_error")
    if isinstance(exc, (TimeoutError, OSError)):
        return TelegramError("telegram_network_or_session_error")
    return TelegramError("telegram_invalid_chat")


@asynccontextmanager
async def telegram_client(settings: Settings, *, login: bool = False):
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise TelegramError("telegram_not_configured")
    path = settings.telegram_session_path.expanduser().absolute()
    if path.suffix != ".session":
        raise TelegramError("telegram_session_path_requires_session_suffix")
    if not login and not path.is_file():
        raise TelegramError("telegram_authorization_required")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    client = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TelegramError("telegram_session_busy") from None
        # Create with restricted permissions before SQLite opens the file.
        session_fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(session_fd)
        path.chmod(0o600)
        client = TelegramClient(
            str(path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            receive_updates=False,
            flood_sleep_threshold=0,
            request_retries=1,
            connection_retries=1,
            timeout=settings.telegram_request_timeout,
            device_model="HSE Study Agent",
            app_version="0.1",
        )
        async with asyncio.timeout(settings.telegram_request_timeout):
            await client.connect()
            if not login and not await client.is_user_authorized():
                raise TelegramError("telegram_authorization_required")
        yield client
    except (errors.RPCError, OSError, TimeoutError, ValueError) as exc:
        raise telegram_error(exc) from None
    finally:
        try:
            if client is not None:
                await client.disconnect()
        finally:
            os.close(descriptor)


def message_url(entity, message_id: int) -> str:
    username = getattr(entity, "username", None)
    if username and isinstance(entity, types.Channel):
        return f"https://t.me/{username}/{message_id}"
    if isinstance(entity, types.Channel):
        return f"https://t.me/c/{entity.id}/{message_id}"
    parameter = "chat_id" if isinstance(entity, types.Chat) else "user_id"
    return f"tg://openmessage?{parameter}={entity.id}&message_id={message_id}"


def pdf_text(data: bytes, settings: Settings) -> tuple[str, str | None]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return "", "telegram_pdf_encrypted"
        parts, length = [], 0
        truncated = len(reader.pages) > settings.telegram_pdf_max_pages
        for page in reader.pages[: settings.telegram_pdf_max_pages]:
            text = page.extract_text() or ""
            available = settings.telegram_pdf_max_chars - length
            parts.append(text[:available])
            length += len(parts[-1])
            if len(text) > available or length >= settings.telegram_pdf_max_chars:
                truncated = True
                break
        text = "\n".join(parts).strip()
        return text, (
            "telegram_pdf_truncated" if truncated else "telegram_pdf_no_text" if not text else None
        )
    except Exception:
        return "", "telegram_pdf_invalid"


async def convert_message(client, entity, message, settings: Settings) -> TelegramMessage | None:
    if not isinstance(message, types.Message):
        return None
    body = message.message or ""
    attachment = None
    file = message.file
    if file and (file.mime_type == "application/pdf" or (file.name or "").lower().endswith(".pdf")):
        attachment = {"name": file.name or "document.pdf", "size": file.size}
        if file.size is None or file.size > settings.telegram_pdf_max_bytes:
            attachment["error"] = "telegram_pdf_too_large"
        else:
            try:
                data = bytearray()
                async with asyncio.timeout(settings.telegram_request_timeout):
                    async for chunk in client.iter_download(message.media, request_size=65536):
                        data.extend(chunk)
                        if len(data) > settings.telegram_pdf_max_bytes:
                            raise TelegramError("telegram_pdf_too_large")
                text, failure = await asyncio.to_thread(pdf_text, bytes(data), settings)
                if text:
                    body += "\n\nТекст PDF-вложения:\n" + text
                if failure:
                    attachment["error"] = failure
            except (TelegramError, errors.RPCError, OSError, TimeoutError) as exc:
                attachment["error"] = telegram_error(exc).code
        if "error" in attachment:
            body += "\n[PDF: текст извлечён не полностью или недоступен]"
    if not body.strip():
        return None
    chat_id = utils.get_peer_id(entity)
    published = message.date.astimezone(UTC)
    edited = message.edit_date.astimezone(UTC).isoformat() if message.edit_date else None
    return TelegramMessage(
        external_id=f"{chat_id}:{message.id}",
        title=f"{getattr(entity, 'title', None) or 'Telegram'} · #{message.id}",
        text=body,
        url=message_url(entity, message.id),
        published_at=published,
        fetched_at=datetime.now(UTC),
        attributes={
            "chat_id": str(chat_id),
            "message_id": message.id,
            "published_at": published.isoformat(),
            "edited_at": edited,
            "reply_to_message_id": message.reply_to_msg_id,
            "grouped_id": message.grouped_id,
            "attachment": attachment,
        },
    )


async def fetch_chat(client, ref: str, cursor: dict, settings: Settings) -> dict:
    """Incremental oldest-first pages; a bounded reread detects recent edits."""
    entity = await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)
    last_id = cursor.get("last_id", 0)
    since = datetime.fromisoformat(cursor["since"])
    kwargs = {"min_id": last_id} if last_id else {"offset_date": since}
    records, seen = {}, []
    async for message in client.iter_messages(
        entity, reverse=True, limit=settings.telegram_batch_size + 1, **kwargs
    ):
        seen.append(message)
    has_more = len(seen) > settings.telegram_batch_size
    for message in seen[: settings.telegram_batch_size]:
        last_id = max(last_id, message.id)
        converted = await convert_message(client, entity, message, settings)
        if converted:
            records[message.id] = converted
    if settings.telegram_edit_scan_limit:
        async for message in client.iter_messages(entity, limit=settings.telegram_edit_scan_limit):
            if message.date < since:
                break
            if message.id > last_id or message.id in records:
                continue
            converted = await convert_message(client, entity, message, settings)
            if converted:
                records[message.id] = converted
    return {
        "records": list(records.values()),
        "has_more": has_more,
        "cursor": {**cursor, "last_id": last_id, "chat_id": str(utils.get_peer_id(entity))},
    }
