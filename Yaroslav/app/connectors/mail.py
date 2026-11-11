"""Чтение студенческой почты по IMAP (по умолчанию Яндекс 360, куда переехала почта ВШЭ).

Коннектор только читает: папки открываются через EXAMINE, письма — через BODY.PEEK,
поэтому статус «прочитано» в ящике не меняется. Пароль приложения берётся из окружения
сервера и не попадает в логи, ошибки или базу.
"""

import asyncio
import email
import hashlib
import imaplib
import re
import ssl
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings
from app.connectors.base import DocumentRecord
from app.connectors.wiki import clean_html

MAX_BODY_CHARS = 60_000


class MailError(Exception):
    """Безопасный код ошибки без текста ответа сервера и учётных данных."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MailBatch(BaseModel):
    messages: list[DocumentRecord] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    skipped: int = 0
    scope: dict[str, Any] = Field(default_factory=dict)
    has_more: bool = False


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def message_text(message: Message) -> tuple[str, list[dict[str, str]], list[str]]:
    """Текст письма, ссылки из HTML и имена вложений (сами вложения не скачиваются)."""
    plain, html, attachments = [], [], []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename or part.get_content_disposition() == "attachment":
            attachments.append(_header(filename) or "без имени")
            continue
        if part.get_content_type() == "text/plain":
            plain.append(_decode_part(part))
        elif part.get_content_type() == "text/html":
            html.append(_decode_part(part))
    links: list[dict[str, str]] = []
    if plain:
        text = "\n".join(plain)
        text = "\n".join(line.rstrip() for line in text.splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    elif html:
        text, raw_links = clean_html("\n".join(html))
        links = [
            {"url": url, "text": label}
            for url, label in raw_links
            if url.startswith(("http://", "https://"))
        ][:100]
    else:
        text = ""
    return text[:MAX_BODY_CHARS], links, attachments


def sender_allowed(address: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    domain = address.rpartition("@")[2].lower()
    return any(domain == item or domain.endswith("." + item) for item in allowed)


def parse_message(raw: bytes, folder: str, settings: Settings, fetched_at: datetime):
    """Вернуть DocumentRecord или None, если отправитель не входит в разрешённые домены."""
    message = email.message_from_bytes(raw)
    sender_name, sender = parseaddr(_header(message.get("From")))
    sender = sender.lower()
    if not sender_allowed(sender, settings.mail_allowed_sender_domains):
        return None
    subject = _header(message.get("Subject")) or "Без темы"
    message_id = (message.get("Message-ID") or "").strip()
    try:
        published_at = parsedate_to_datetime(message.get("Date"))
        if published_at.tzinfo is None:
            published_at = None
    except (TypeError, ValueError, IndexError):
        published_at = None
    text, links, attachments = message_text(message)
    # Message-ID стабилен между папками и повторными синхронизациями; без него —
    # хеш ключевых заголовков, чтобы повторный обход не создавал дубликатов.
    identity = message_id or "|".join(
        [sender, subject, message.get("Date") or "", hashlib.sha256(raw).hexdigest()]
    )
    external_id = "mail:" + hashlib.sha256(identity.encode()).hexdigest()[:40]
    recipients = [addr for _, addr in getaddresses(message.get_all("To", []))][:20]
    header = [
        f"От: {sender_name} <{sender}>" if sender_name else f"От: {sender}",
        f"Тема: {subject}",
    ]
    if published_at:
        header.append("Дата: " + published_at.isoformat())
    if attachments:
        header.append("Вложения: " + ", ".join(attachments[:20]))
    body = "\n".join(header) + "\n\n" + text
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    return DocumentRecord(
        source_id="mail",
        external_id=external_id,
        title=subject[:500],
        text=body,
        url=settings.mail_web_url,
        published_at=published_at,
        fetched_at=fetched_at,
        content_type="message/rfc822",
        raw_content=body,
        revision_key="message",
        content_hash=content_hash,
        links=links,
        attributes={
            "kind": "message",
            "folder": folder,
            "sender": sender,
            "sender_name": sender_name,
            "recipients": recipients,
            "attachments": attachments[:20],
            "has_message_id": bool(message_id),
        },
    )


def _imap_date(value: datetime) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{value.day:02d}-{months[value.month - 1]}-{value.year}"


def _quote_folder(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MailConnector:
    source_id = "mail"

    def __init__(self, settings: Settings):
        if not settings.mail_username or settings.mail_password is None:
            raise MailError("mail_not_configured")
        self.settings = settings

    def _connect(self) -> imaplib.IMAP4:
        s = self.settings
        try:
            if s.mail_imap_ssl:
                client = imaplib.IMAP4_SSL(
                    s.mail_imap_host,
                    s.mail_imap_port,
                    ssl_context=ssl.create_default_context(),
                    timeout=s.mail_request_timeout,
                )
            else:
                client = imaplib.IMAP4(s.mail_imap_host, s.mail_imap_port, s.mail_request_timeout)
        except TimeoutError:
            raise MailError("mail_timeout") from None
        except (OSError, imaplib.IMAP4.error):
            raise MailError("mail_network_error") from None
        try:
            client.login(s.mail_username, s.mail_password.get_secret_value())
        except imaplib.IMAP4.error:
            # Текст ответа сервера может содержать логин — не пробрасываем его.
            raise MailError("mail_auth_failed") from None
        except TimeoutError:
            raise MailError("mail_timeout") from None
        except OSError:
            raise MailError("mail_network_error") from None
        return client

    def collect_sync(self) -> MailBatch:
        s = self.settings
        fetched_at = datetime.now(UTC)
        since = _imap_date(fetched_at - timedelta(days=s.mail_lookback_days))
        batch = MailBatch(
            scope={
                "host": s.mail_imap_host,
                "folders": s.mail_folders,
                "lookback_days": s.mail_lookback_days,
                "max_messages": s.mail_max_messages,
                "allowed_sender_domains": s.mail_allowed_sender_domains,
            }
        )
        seen: set[str] = set()
        client = self._connect()
        try:
            for folder in s.mail_folders:
                try:
                    status, _ = client.select(_quote_folder(folder), readonly=True)
                    if status != "OK":
                        batch.errors.append({"folder": folder, "code": "mail_folder_unavailable"})
                        continue
                    status, data = client.uid("SEARCH", None, "SINCE", since)
                    if status != "OK":
                        batch.errors.append({"folder": folder, "code": "mail_search_failed"})
                        continue
                    uids = data[0].split() if data and data[0] else []
                    budget = s.mail_max_messages - len(batch.messages)
                    if len(uids) > budget:
                        batch.has_more = True
                    # Новые письма важнее: берём последние UID.
                    for uid in reversed(uids[-budget:] if budget > 0 else []):
                        status, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
                        raw = next(
                            (p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1),
                            None,
                        )
                        if status != "OK" or not isinstance(raw, bytes):
                            batch.errors.append({"folder": folder, "code": "mail_fetch_failed"})
                            continue
                        record = parse_message(raw, folder, s, fetched_at)
                        if record is None:
                            batch.skipped += 1
                        elif record.external_id not in seen:
                            seen.add(record.external_id)
                            batch.messages.append(record)
                except TimeoutError:
                    batch.errors.append({"folder": folder, "code": "mail_timeout"})
                    break
                except (OSError, imaplib.IMAP4.error):
                    batch.errors.append({"folder": folder, "code": "mail_network_error"})
                    break
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        return batch

    async def collect(self) -> MailBatch:
        return await asyncio.to_thread(self.collect_sync)

    async def fetch(self) -> list[DocumentRecord]:
        return (await self.collect()).messages
