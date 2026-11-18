"""Публичные каналы, сообщения групп через бота, напоминания о сроках и /plan.

Состояние бота (offset обновлений, чат владельца, отправленные напоминания) хранится
в PostgreSQL, в config источника telegram_bot: перезапуск не теряет сообщения и не
повторяет уже отправленные напоминания.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.dialects.postgresql import insert

from app.api.schemas import Task
from app.config import Settings
from app.connectors.telegram_bot import BotApi, message_record
from app.connectors.telegram_public import PublicChannelError, channel_name, fetch_channel
from app.db.models import IngestionSource
from app.db.session import Database
from app.services.dsba import dsba_tasks, task_sort_key
from app.services.ingestion import store_page
from app.services.personal_sync import Batch, sync_personal_source
from app.services.planner import build_plan

BOT_SOURCE = "telegram_bot"


async def sync_public_channels(database: Database, settings: Settings) -> dict:
    async def collect() -> Batch:
        records, errors = [], []
        async with httpx.AsyncClient(
            timeout=settings.telegram_request_timeout,
            follow_redirects=True,
            headers={"User-Agent": "HSEStudyAgent/0.1 (public channel reader)"},
        ) as client:
            for ref in settings.telegram_public_channels:
                try:
                    records.extend(await fetch_channel(client, ref, settings))
                except PublicChannelError as exc:
                    errors.append({"chat": ref, "code": exc.code})
        return Batch(records, errors)

    return await sync_personal_source(
        database,
        source_id="telegram_channels",
        kind="telegram_channels",
        name="Telegram-каналы",
        base_url="https://t.me/s",
        scope={
            "channels": [channel_name(ref) for ref in settings.telegram_public_channels],
            "history_days": settings.telegram_history_days,
            "interval_seconds": settings.telegram_public_interval_seconds,
        },
        collect=collect,
    )


async def load_state(database: Database) -> dict:
    async with database.session() as session, session.begin():
        await session.execute(
            insert(IngestionSource)
            .values(
                id=BOT_SOURCE,
                kind="telegram_bot",
                name="Telegram-бот",
                base_url="https://t.me",
                config={},
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        source = await session.get(IngestionSource, BOT_SOURCE)
        return dict(source.config or {})


async def save_state(
    database: Database, state: dict, *, synced: bool = False, error: str | None = None
):
    async with database.session() as session, session.begin():
        source = await session.get(IngestionSource, BOT_SOURCE)
        source.config = dict(state)
        source.last_error = error
        if synced:
            source.last_synced_at = datetime.now(UTC)


def is_owner(message: dict, settings: Settings) -> bool:
    owner = (settings.telegram_bot_owner or "").lstrip("@").lower()
    sender = ((message.get("from") or {}).get("username") or "").lower()
    return bool(owner) and sender == owner and (message.get("chat") or {}).get("type") == "private"


async def owner_tasks(database: Database, settings: Settings) -> list[Task]:
    tasks = await dsba_tasks(database, "smartlms", kind="smartlms")
    if settings.telegram_reminder_dsba_source:
        tasks += await dsba_tasks(database, settings.telegram_reminder_dsba_source)
    return sorted(tasks, key=task_sort_key)


def due_moment(task: Task, zone: ZoneInfo) -> datetime | None:
    if task.due_at is not None:
        return task.due_at
    if task.due_date is not None:
        # Только для расчёта момента напоминания; срок в тексте остаётся датой.
        return datetime.combine(task.due_date, time(23, 59), zone)
    return None


def due_text(task: Task, zone: ZoneInfo) -> str:
    if task.due_at is not None:
        return task.due_at.astimezone(zone).strftime("%d.%m %H:%M")
    return task.due_date.strftime("%d.%m") + " (время не указано)"


async def plan_message(database: Database, settings: Settings, now: datetime) -> str:
    tasks, plan = build_plan(
        await owner_tasks(database, settings), now, settings.telegram_plan_minutes, set()
    )
    by_id = {task.id: task for task in tasks}
    zone = ZoneInfo(settings.timezone)
    lines = [f"План на {now.strftime('%d.%m')}, {settings.telegram_plan_minutes} мин:"]
    for block in plan.blocks:
        task = by_id[block.task_id]
        lines.append(
            f"• {block.starts_at.strftime('%H:%M')} — {task.title} ({task.course}), "
            f"срок {due_text(task, zone)}\n  {block.reason}"
        )
    if not plan.blocks:
        lines.append("Ничего не запланировано.")
    if plan.unscheduled_task_ids:
        lines.append(
            "Не поместилось: " + ", ".join(by_id[i].title for i in plan.unscheduled_task_ids)
        )
    lines.append(plan.note)
    return "\n".join(lines)


async def process_updates(
    database: Database, settings: Settings, api: BotApi, timeout: int
) -> dict:
    state = await load_state(database)
    updates = await api.get_updates(state.get("offset"), timeout)
    stored = {"created": 0, "updated": 0, "unchanged": 0}
    replies = 0
    for update in updates:
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
            or {}
        )
        record = message_record(message) if message else None
        if record is not None:
            stored[await store_page(database, record)] += 1
        elif message and (message.get("chat") or {}).get("type") == "private":
            text = (message.get("text") or "").strip()
            chat_id = message["chat"]["id"]
            if not is_owner(message, settings):
                await api.send_message(
                    chat_id, "Это личный бот учебного агента. Он отвечает только владельцу."
                )
            elif text.startswith("/start"):
                state["owner_chat_id"] = chat_id
                await api.send_message(
                    chat_id,
                    "Готово: напоминания о сроках будут приходить сюда. "
                    "Команда /plan — план на сегодня.",
                )
            elif text.startswith("/plan"):
                now = datetime.now(ZoneInfo(settings.timezone))
                await api.send_message(chat_id, await plan_message(database, settings, now))
            else:
                await api.send_message(chat_id, "Команды: /plan — план на сегодня.")
            replies += 1
        # Offset сохраняется после обработки: при сбое обновление придёт снова,
        # а повторная запись того же сообщения даёт «unchanged», а не дубль.
        state["offset"] = update["update_id"] + 1
        await save_state(database, state, synced=True)
    if not updates:
        await save_state(database, state, synced=True)
    return {"updates": len(updates), **stored, "replies": replies}


def reminders_due(
    tasks: list[Task], now: datetime, hours: list[int], sent: dict, zone: ZoneInfo
) -> list[tuple[str, Task, int]]:
    """Какие напоминания отправить сейчас: одно на срок и окно, ближайшее окно приоритетно."""
    result = []
    for task in tasks:
        due = due_moment(task, zone)
        if due is None or task.status == "done" or due <= now:
            continue
        open_windows = [h for h in sorted(hours) if due - timedelta(hours=h) <= now]
        if not open_windows:
            continue
        due_key = due.isoformat()
        keys = [f"{task.id}:{due_key}:{h}" for h in open_windows]
        if all(key in sent for key in keys):
            continue
        # Если пропущено несколько окон (бот был выключен), шлём одно — самое близкое.
        result.append((keys[0], task, open_windows[0]))
        for key in keys[1:]:
            sent.setdefault(key, now.isoformat())
    return result


async def send_reminders(database: Database, settings: Settings, api: BotApi) -> dict:
    state = await load_state(database)
    chat_id = state.get("owner_chat_id")
    if not chat_id:
        return {"skipped": "owner_not_started"}
    zone = ZoneInfo(settings.timezone)
    now = datetime.now(zone)
    sent = dict(state.get("sent_reminders", {}))
    due = reminders_due(
        await owner_tasks(database, settings), now, settings.telegram_reminder_hours, sent, zone
    )
    delivered = 0
    for key, task, _ in due:
        left = due_moment(task, zone) - now
        hours_left = max(1, round(left.total_seconds() / 3600))
        changed = f"\n{task.deadline_changed}" if task.deadline_changed else ""
        await api.send_message(
            chat_id,
            f"⏰ Через ~{hours_left} ч срок: {task.title}\nКурс: {task.course}\n"
            f"Срок: {due_text(task, zone)}{changed}\n{task.evidence[0].url or ''}",
        )
        sent[key] = now.isoformat()
        delivered += 1
        state["sent_reminders"] = sent
        await save_state(database, state, synced=True)
    # Старые отметки больше не нужны: срок прошёл.
    cutoff = (now - timedelta(days=30)).isoformat()
    state["sent_reminders"] = {k: v for k, v in sent.items() if v >= cutoff}
    await save_state(database, state, synced=True)
    return {"sent": delivered}
