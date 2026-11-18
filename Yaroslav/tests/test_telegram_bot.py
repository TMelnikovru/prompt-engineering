import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text

from app.api.schemas import Evidence, Task
from app.config import Settings
from app.connectors.base import DocumentRecord
from app.connectors.telegram_bot import BotApi, BotError, message_record
from app.connectors.telegram_public import (
    PublicChannelError,
    channel_name,
    fetch_channel,
    parse_channel_page,
)
from app.db.session import Database
from app.services.ingestion import store_page
from app.services.telegram_bot import process_updates, reminders_due, send_reminders

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)

PAGE = """<html><div class="tgme_channel_info_header_title"><span>DSBA HSE</span></div>
<a class="tme_messages_more js-messages_more" data-before="40" href="/s/dsba_hse?before=40"></a>
<div class="tgme_widget_message" data-post="dsba_hse/41">
 <div class="tgme_widget_message_text">Дедлайн ДЗ 3 перенесён<br>на 25 ноября.
 <a href="https://edu.hse.ru/x">ссылка</a></div>
 <div class="tgme_widget_message_document_title">hw3.pdf</div>
 <span class="tgme_widget_message_meta">edited <a class="tgme_widget_message_date">
 <time datetime="2026-11-17T10:00:00+00:00">13:00</time></a></span>
</div>
<div class="tgme_widget_message" data-post="dsba_hse/42">
 <a class="tgme_widget_message_date"><time datetime="2026-11-18T09:00:00+00:00">12:00</time></a>
</div></html>"""


def test_channel_name_accepts_common_forms_and_rejects_invites():
    for ref in ("@dsba_hse", "dsba_hse", "t.me/dsba_hse", "https://t.me/s/dsba_hse"):
        assert channel_name(ref) == "dsba_hse"
    with pytest.raises(PublicChannelError):
        channel_name("https://t.me/+AbCdEf")


def test_channel_page_becomes_posts_with_links_files_and_edit_flag():
    title, records, before = parse_channel_page(PAGE, "dsba_hse", NOW)
    assert title == "DSBA HSE" and before == 40
    assert len(records) == 1  # пост без текста и файлов пропускается
    post = records[0]
    assert post.external_id == "@dsba_hse:41" and post.url == "https://t.me/dsba_hse/41"
    assert post.text.startswith("Дедлайн ДЗ 3 перенесён\nна 25 ноября.")
    assert "Файлы: hw3.pdf" in post.text
    assert post.attributes["edited"] is True
    assert post.attributes["chat_id"] == "@dsba_hse"
    assert {"url": "https://edu.hse.ru/x", "text": "ссылка"} in post.links


def test_closed_or_missing_channel_is_an_explicit_error():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>Telegram</html>"))
    )
    with pytest.raises(PublicChannelError) as error:
        asyncio.run(fetch_channel(client, "@closed_one", Settings(_env_file=None), NOW))
    assert error.value.code == "telegram_channel_not_public"


def group_message(message_id=7, chat_type="supergroup", **extra):
    return {
        "message_id": message_id,
        "date": int(NOW.timestamp()),
        "chat": {"id": -1001234567890, "type": chat_type, "title": "Группа 261"},
        "from": {"username": "classmate"},
        "text": "Коллоквиум в пятницу",
        **extra,
    }


def test_group_messages_are_stored_private_chats_are_not():
    record = message_record(group_message())
    assert record.external_id == "-1001234567890:7"
    assert record.url == "https://t.me/c/1234567890/7"
    assert record.attributes["author"] == "classmate"
    assert message_record(group_message(chat_type="private")) is None


def test_bot_token_never_appears_in_errors():
    settings = Settings(_env_file=None, telegram_bot_token="123:secret-token")

    def handler(request):
        raise httpx.ConnectError("boom " + str(request.url))

    api = BotApi(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(BotError) as error:
        asyncio.run(api.get_me())
    assert error.value.code == "telegram_bot_network_error"
    assert "secret-token" not in str(error.value) and error.value.__cause__ is None


def task(task_id, due_at):
    return Task(
        id=task_id,
        title=task_id,
        course="Курс",
        priority="medium",
        due_at=due_at,
        evidence=[
            Evidence(
                source_id="smartlms",
                title=task_id,
                quote="q",
                url="https://edu.hse.ru",
                fetched_at=NOW,
            )
        ],
    )


def test_reminders_send_once_per_window_and_once_after_downtime():
    now = NOW.astimezone(MSK)
    soon = task("soon", now + timedelta(hours=2))  # оба окна (24 и 3 ч) уже открыты
    later = task("later", now + timedelta(hours=20))
    far = task("far", now + timedelta(days=5))
    sent: dict = {}
    first = reminders_due([soon, later, far], now, [24, 3], sent, MSK)
    assert [(t.id, h) for _, t, h in first] == [("soon", 3), ("later", 24)]
    sent.update({key: now.isoformat() for key, _, _ in first})
    assert reminders_due([soon, later, far], now, [24, 3], sent, MSK) == []
    # Перенос срока — новое напоминание.
    moved = task("later", now + timedelta(hours=10))
    assert [t.id for _, t, _ in reminders_due([moved], now, [24, 3], sent, MSK)] == ["later"]


class FakeApi:
    def __init__(self, updates):
        self.updates, self.sent = updates, []

    async def get_updates(self, offset, timeout):
        pending = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return pending

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {}


@pytest.mark.integration
def test_bot_stores_groups_registers_owner_plans_and_reminds_once(ingestion_url):
    settings = Settings(_env_file=None, database_url=ingestion_url, telegram_bot_owner="@Yaroslav")
    owner = {"chat": {"id": 555, "type": "private"}, "from": {"username": "yaroslav"}}

    async def run():
        database = Database(settings)
        try:
            async with database.session() as session, session.begin():
                await session.execute(
                    text(
                        "TRUNCATE ai_artifacts, document_versions, source_documents, "
                        "ingestion_runs, ingestion_sources CASCADE"
                    )
                )
            api = FakeApi(
                [
                    {"update_id": 1, "message": group_message()},
                    {
                        "update_id": 2,
                        "message": {**owner, "message_id": 1, "date": 0, "text": "/start"},
                    },
                    {
                        "update_id": 3,
                        "message": {
                            "chat": {"id": 9, "type": "private"},
                            "from": {"username": "stranger"},
                            "message_id": 1,
                            "date": 0,
                            "text": "/plan",
                        },
                    },
                ]
            )
            first = await process_updates(database, settings, api, timeout=0)
            again = await process_updates(database, settings, api, timeout=0)
            async with database.session() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO ingestion_sources (id, kind, name, base_url, config) "
                        "VALUES ('smartlms', 'smartlms', 'SmartLMS', 'https://edu.hse.ru', '{}') "
                        "ON CONFLICT DO NOTHING"
                    )
                )
            due = datetime.now(UTC) + timedelta(hours=2)
            await store_page(
                database,
                DocumentRecord(
                    source_id="smartlms",
                    external_id="deadline:assign:1",
                    title="ДЗ 4 — Алгоритмы",
                    text="Срок",
                    url="https://edu.hse.ru/mod/assign/view.php?id=1",
                    fetched_at=datetime.now(UTC),
                    raw_content="Срок",
                    revision_key="event",
                    content_hash="h1",
                    attributes={
                        "kind": "deadline",
                        "facts": {
                            "title": "ДЗ 4",
                            "course": "Алгоритмы",
                            "due_at": due.isoformat(),
                            "due_raw": due.isoformat(),
                        },
                    },
                ),
            )
            reminders = await send_reminders(database, settings, api)
            repeat = await send_reminders(database, settings, api)
            plan_api = FakeApi(
                [
                    {
                        "update_id": 4,
                        "message": {**owner, "message_id": 2, "date": 0, "text": "/plan"},
                    }
                ]
            )
            await process_updates(database, settings, plan_api, timeout=0)
            return first, again, reminders, repeat, api.sent, plan_api.sent
        finally:
            await database.close()

    first, again, reminders, repeat, sent, plan_sent = asyncio.run(run())
    assert first == {"updates": 3, "created": 1, "updated": 0, "unchanged": 0, "replies": 2}
    assert again["updates"] == 0  # offset сохранён в базе
    assert sent[0][0] == 555 and "напоминания" in sent[0][1]
    assert sent[1][0] == 9 and "только владельцу" in sent[1][1]
    assert reminders == {"sent": 1} and repeat == {"sent": 0}
    assert "ДЗ 4" in sent[2][1] and sent[2][0] == 555
    assert plan_sent[0][0] == 555 and "ДЗ 4" in plan_sent[0][1]
