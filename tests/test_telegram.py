import asyncio
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from telethon.tl import types

from app.config import Settings
from app.connectors.telegram import fetch_chat, pdf_text
from app.main import create_app
from app.services.analysis import AiError
from app.services.telegram import ChatPoint, TelegramDigest, validate_digest


def channel():
    return types.Channel(
        id=123,
        title="Учебная группа",
        photo=types.ChatPhotoEmpty(),
        date=datetime.now(UTC),
        megagroup=True,
    )


def message(number, text=None, *, edit_date=None):
    return types.Message(
        id=number,
        peer_id=types.PeerChannel(123),
        date=datetime.now(UTC) - timedelta(hours=1) + timedelta(seconds=number),
        message=text or f"Сообщение {number}",
        edit_date=edit_date,
    )


class TelegramTransport:
    """MTProto transport double: HTTP simulations cannot serve the Telegram protocol."""

    def __init__(self, messages):
        self.messages, self.requested = messages, []

    async def get_entity(self, ref):
        self.requested.append(ref)
        if ref == "@unavailable":
            raise ValueError("Synthetic inaccessible chat")
        return channel()

    async def iter_messages(self, entity, *, limit, reverse=False, min_id=0, offset_date=None):
        messages = sorted(self.messages, key=lambda item: item.id, reverse=not reverse)
        messages = [item for item in messages if item.id > min_id]
        if offset_date is not None:
            messages = [item for item in messages if item.date > offset_date]
        for item in messages[:limit]:
            yield item


def test_chat_allowlist_normalizes_links_and_rejects_invites():
    settings = Settings(
        _env_file=None, telegram_chats=["https://t.me/DSBA_HSE", "@dsba_hse", -100123]
    )
    assert settings.telegram_chats == ["@dsba_hse", "-100123"]
    for ref in ("https://evil.test/chat", "https://t.me/+invite", "https://t.me/chat/123", True):
        with pytest.raises(ValueError):
            Settings(_env_file=None, telegram_chats=[ref])


def test_pagination_does_not_skip_backlog_and_rereads_recent_edits():
    async def run():
        settings = Settings(_env_file=None, telegram_batch_size=2, telegram_edit_scan_limit=4)
        client = TelegramTransport([message(n) for n in range(1, 6)])
        cursor = {"last_id": 0, "since": (datetime.now(UTC) - timedelta(days=7)).isoformat()}
        first = await fetch_chat(client, "@selected", cursor, settings)
        assert first["cursor"]["last_id"] == 2 and first["has_more"]
        assert {record.external_id for record in first["records"]} == {
            "-1000000000123:1",
            "-1000000000123:2",
        }
        second = await fetch_chat(client, "@selected", first["cursor"], settings)
        assert second["cursor"]["last_id"] == 4 and second["has_more"]
        last = await fetch_chat(client, "@selected", second["cursor"], settings)
        assert last["cursor"]["last_id"] == 5 and not last["has_more"]
        before = {item.external_id: item.content_hash for item in last["records"]}
        client.messages[3].message = "Срок перенесён на пятницу"
        client.messages[3].edit_date = datetime.now(UTC)
        edited = await fetch_chat(client, "@selected", last["cursor"], settings)
        changed = next(
            record for record in edited["records"] if record.attributes["message_id"] == 4
        )
        assert changed.content_hash != before[changed.external_id]
        assert client.requested == ["@selected"] * 4
        assert changed.published_at.utcoffset() == timedelta(0)
        assert changed.url == "https://t.me/c/123/4"

    asyncio.run(run())


def test_digest_rejects_invented_quotes_references_and_dates():
    raw = {
        "summary": "Учебные задания",
        "topics": [],
        "agreements": [],
        "open_questions": [],
        "conflicts": [],
        "assignments": [
            {
                "title": "Домашняя работа",
                "due_text": "пятницу",
                "evidence_quote": "Сдать в пятницу",
                "message_id": "123:1",
            }
        ],
    }
    content = [{"message_id": "123:1", "text": "Сдать в пятницу"}]
    result = TelegramDigest.model_validate(raw)
    validate_digest(result, content)
    result.assignments[0].due_text = "23:59"
    with pytest.raises(AiError, match="unsupported_telegram_deadline"):
        validate_digest(result, content)
    result.assignments[0].due_text = None
    result.assignments[0].evidence_quote = "Сдать завтра"
    with pytest.raises(AiError, match="unsupported_telegram_evidence"):
        validate_digest(result, content)
    result.assignments = []
    result.topics = [ChatPoint(text="Тема", message_ids=["unknown:1"])]
    with pytest.raises(AiError, match="unsupported_telegram_citation"):
        validate_digest(result, content)


def test_pdf_invalid_and_scanned_documents_are_explicit():
    settings = Settings(_env_file=None)
    assert pdf_text(b"not a PDF", settings) == ("", "telegram_pdf_invalid")
    writer, target = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=100, height=100)
    writer.write(target)
    assert pdf_text(target.getvalue(), settings) == ("", "telegram_pdf_no_text")


def test_private_library_requires_operator_token_before_database_access():
    settings = Settings(_env_file=None, ingestion_token="local-test-token")
    with TestClient(create_app(settings)) as api:
        for path in (
            "/sources",
            "/documents?source_id=telegram",
            "/documents/00000000-0000-0000-0000-000000000001",
            "/knowledge/summary",
            "/knowledge/telegram",
        ):
            assert api.get("/api/v1" + path).status_code == 401
        for path in ("/integrations/telegram/sync", "/knowledge/telegram/refresh"):
            assert api.post("/api/v1" + path).status_code == 401
            assert (
                api.post(
                    "/api/v1" + path, headers={"X-Ingestion-Token": "local-test-token"}
                ).status_code
                == 503
            )
