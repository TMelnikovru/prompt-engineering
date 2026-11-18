"""Telegram-бот и открытые каналы: check — проверка без записи в базу, worker — работа.

worker читает обновления бота (long polling), раз в telegram_public_interval_seconds
обновляет каналы и каждые 5 минут проверяет, пора ли напомнить о сроке.
"""

import argparse
import asyncio
import json
import time

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.telegram_bot import BotApi, BotError
from app.connectors.telegram_public import PublicChannelError, fetch_channel
from app.db.session import Database
from app.ingest import stage_error
from app.runtime import run as run_async
from app.services.ingestion import SyncBusy
from app.services.telegram_bot import (
    load_state,
    process_updates,
    save_state,
    send_reminders,
    sync_public_channels,
)

REMINDER_EVERY = 300


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def check(settings: Settings) -> int:
    result: dict = {}
    if settings.telegram_bot_token is not None:
        try:
            async with BotApi(settings) as api:
                me = await api.get_me()
            result["bot"] = {
                "username": me.get("username"),
                "can_read_all_group_messages": me.get("can_read_all_group_messages"),
            }
        except BotError as exc:
            result["bot"] = {"error": exc.code}
    else:
        result["bot"] = {"error": "telegram_bot_not_configured"}
    channels = {}
    async with httpx.AsyncClient(
        timeout=settings.telegram_request_timeout, follow_redirects=True
    ) as client:
        for ref in settings.telegram_public_channels:
            try:
                posts = await fetch_channel(client, ref, settings)
                channels[ref] = {
                    "posts": len(posts),
                    "latest": posts[-1].published_at.isoformat() if posts else None,
                }
            except PublicChannelError as exc:
                channels[ref] = {"error": exc.code}
    result["channels"] = channels
    emit(result)
    return 0 if "error" not in result["bot"] or channels else 2


async def worker(database: Database, settings: Settings) -> None:
    last_channels = last_reminders = 0.0
    failures = 0
    while True:
        cycle: dict = {"event": "telegram_bot_cycle"}
        now = time.monotonic()
        if (
            settings.telegram_public_channels
            and now - last_channels >= settings.telegram_public_interval_seconds
        ):
            try:
                cycle["channels"] = await sync_public_channels(database, settings)
            except (SyncBusy, SQLAlchemyError) as exc:
                cycle["channels"] = stage_error(exc)
            last_channels = now
        if settings.telegram_bot_token is not None:
            try:
                async with BotApi(settings) as api:
                    cycle["updates"] = await process_updates(database, settings, api, timeout=25)
                    if time.monotonic() - last_reminders >= REMINDER_EVERY:
                        cycle["reminders"] = await send_reminders(database, settings, api)
                        last_reminders = time.monotonic()
                failures = 0
            except BotError as exc:
                failures += 1
                cycle["error"] = exc.code
                try:
                    await save_state(database, await load_state(database), error=exc.code)
                except SQLAlchemyError:
                    pass
            except SQLAlchemyError as exc:
                failures += 1
                cycle["error"] = stage_error(exc)["error"]
        if cycle.keys() - {"event"}:
            if cycle.get("updates", {}).get("updates") or "updates" not in cycle or failures:
                emit(cycle)
        if settings.telegram_bot_token is None or failures:
            await asyncio.sleep(min(300, 5 * 2 ** min(failures, 6)) if failures else 30)


async def execute(args) -> int:
    settings = Settings()
    if args.command == "check":
        return await check(settings)
    if settings.database_url is None:
        emit({"error": "database_not_configured"})
        return 2
    if settings.telegram_bot_token is None and not settings.telegram_public_channels:
        while True:
            emit({"event": "telegram_bot_cycle", "skipped": "telegram_bot_not_configured"})
            await asyncio.sleep(settings.telegram_public_interval_seconds)
    database = Database(settings)
    try:
        await worker(database, settings)
    finally:
        await database.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("worker")
    try:
        raise SystemExit(run_async(execute(parser.parse_args())))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
