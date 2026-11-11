"""CLI и серверный воркер почты: login — вход через Яндекс/ЕЛК и запись токена в .env,
check — проверка ящика без записи в базу, sync — один проход, worker — по расписанию."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.mail import MailConnector, MailError
from app.connectors.yandex_oauth import start_device_flow, wait_for_token, write_env_value
from app.db.session import Database
from app.ingest import analyze_pending, stage_error
from app.services.analysis import AiError
from app.services.ingestion import SyncBusy
from app.services.mail import sync_mail


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def cycle(database: Database, settings: Settings, analyze: bool, limit: int) -> dict:
    result: dict = {"event": "mail_cycle"}
    try:
        result["sync"] = await sync_mail(database, settings)
    except (SyncBusy, SQLAlchemyError) as exc:
        result["sync"] = stage_error(exc)
    if analyze:
        # Общая очередь ИИ-извлечения: письма обрабатываются тем же этапом, что и вики.
        try:
            result["analysis"] = await analyze_pending(database, settings, limit)
        except (AiError, SyncBusy, SQLAlchemyError) as exc:
            result["analysis"] = stage_error(exc)
    result["retry_required"] = result["sync"].get("status") not in {"success"}
    return result


async def login(settings: Settings, env_file: Path) -> int:
    """Вход через ЕЛК делает сам студент в браузере; токен сразу пишется в .env."""
    try:
        async with httpx.AsyncClient(timeout=settings.mail_request_timeout) as client:
            code = await start_device_flow(settings, client)
            print(
                f"Откройте {code.verification_url}, войдите через ЕЛК ВШЭ и введите код: "
                f"{code.user_code}\nЖдём подтверждения {code.expires_in // 60} мин…",
                file=sys.stderr,
                flush=True,
            )
            token = await wait_for_token(settings, client, code)
    except MailError as exc:
        emit({"error": exc.code})
        return 2
    write_env_value(env_file, "HSE_MAIL_OAUTH_TOKEN", token.access_token)
    days = token.expires_in // 86400 if token.expires_in else None
    emit({"saved_to": str(env_file), "expires_in_days": days})
    return 0


async def execute(args) -> int:
    settings = Settings()
    if args.command == "login":
        return await login(settings, Path(args.env_file))
    if args.command == "check":
        try:
            batch = await MailConnector(settings).collect()
        except MailError as exc:
            emit({"error": exc.code})
            return 2
        emit(
            {
                "messages": len(batch.messages),
                "skipped_by_sender": batch.skipped,
                "errors": batch.errors,
                "has_more": batch.has_more,
                "subjects": [m.title for m in batch.messages[:10]],
            }
        )
        return 2 if batch.errors and not batch.messages else 0
    if settings.database_url is None:
        emit({"error": "database_not_configured"})
        return 2
    configured = settings.mail_username and (settings.mail_password or settings.mail_oauth_token)
    if args.command == "worker" and not configured:
        # Без ящика воркер не пишет в базу ложные сбои: источник просто не подключён.
        while True:
            emit({"event": "mail_cycle", "skipped": "mail_not_configured"})
            await asyncio.sleep(settings.mail_interval_seconds)
    database = Database(settings)
    try:
        if args.command == "sync":
            result = await cycle(database, settings, args.analyze, args.limit)
            emit(result)
            return 2 if result["retry_required"] else 0
        failures = 0
        while True:
            result = await cycle(database, settings, args.analyze, args.limit)
            failures = failures + 1 if result["retry_required"] else 0
            delay = (
                min(
                    settings.mail_interval_seconds,
                    settings.mail_retry_seconds * 2 ** min(failures - 1, 10),
                )
                if failures
                else settings.mail_interval_seconds
            )
            emit({**result, "next_attempt_in_seconds": delay})
            await asyncio.sleep(delay)
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    login_command = commands.add_parser("login")
    login_command.add_argument("--env-file", default=".env")
    for name in ("sync", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--analyze", action="store_true")
        command.add_argument("--limit", type=int, choices=range(1, 51), default=10)
    try:
        raise SystemExit(asyncio.run(execute(parser.parse_args())))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
