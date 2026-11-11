"""CLI и серверный воркер SmartLMS: check — проверка ключа без записи в базу,
sync — один проход, worker — по расписанию."""

import argparse
import asyncio
import json

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.smartlms import SmartLmsConnector, SmartLmsError
from app.db.session import Database
from app.ingest import stage_error
from app.services.ingestion import SyncBusy
from app.services.smartlms import sync_smartlms


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def check(settings: Settings) -> int:
    try:
        async with SmartLmsConnector(settings) as connector:
            info = await connector.site_info()
            records, has_more = await connector.deadlines()
    except SmartLmsError as exc:
        emit({"error": exc.code})
        return 2
    emit(
        {
            "site": info.get("sitename"),
            "deadlines": len(records),
            "has_more": has_more,
            "nearest": [
                {"title": r.attributes["facts"]["title"], "due_at": r.attributes["facts"]["due_at"]}
                for r in records[:10]
            ],
        }
    )
    return 0


async def cycle(database: Database, settings: Settings) -> dict:
    try:
        sync = await sync_smartlms(database, settings)
    except (SyncBusy, SQLAlchemyError) as exc:
        sync = stage_error(exc)
    return {
        "event": "smartlms_cycle",
        "sync": sync,
        "retry_required": sync.get("status") != "success",
    }


async def execute(args) -> int:
    settings = Settings()
    if args.command == "check":
        return await check(settings)
    if settings.database_url is None:
        emit({"error": "database_not_configured"})
        return 2
    if args.command == "worker" and settings.smartlms_token is None:
        # Без ключа воркер не пишет в базу ложные сбои: источник просто не подключён.
        while True:
            emit({"event": "smartlms_cycle", "skipped": "smartlms_not_configured"})
            await asyncio.sleep(settings.smartlms_interval_seconds)
    database = Database(settings)
    try:
        if args.command == "sync":
            result = await cycle(database, settings)
            emit(result)
            return 2 if result["retry_required"] else 0
        failures = 0
        while True:
            result = await cycle(database, settings)
            failures = failures + 1 if result["retry_required"] else 0
            delay = (
                min(
                    settings.smartlms_interval_seconds,
                    settings.smartlms_retry_seconds * 2 ** min(failures - 1, 10),
                )
                if failures
                else settings.smartlms_interval_seconds
            )
            emit({**result, "next_attempt_in_seconds": delay})
            await asyncio.sleep(delay)
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "sync", "worker"):
        commands.add_parser(name)
    try:
        raise SystemExit(asyncio.run(execute(parser.parse_args())))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
