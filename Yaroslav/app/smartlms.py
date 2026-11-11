"""CLI и серверный воркер SmartLMS: login — вход через ЕЛК и запись ключа в .env,
check — проверка ключа без записи в базу, sync — один проход, worker — по расписанию."""

import argparse
import asyncio
import getpass
import json
import secrets
import sys
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.smartlms import (
    SmartLmsConnector,
    SmartLmsError,
    launch_url,
    password_token,
    token_from_launch,
)
from app.connectors.yandex_oauth import write_env_value
from app.db.session import Database
from app.ingest import stage_error
from app.runtime import run as run_async
from app.services.ingestion import SyncBusy
from app.services.smartlms import collect_deadlines, configured, sync_smartlms


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def save_checked_token(settings: Settings, env_file: Path, token: str) -> int:
    checked = settings.model_copy(update={"smartlms_token": SecretStr(token)})
    async with SmartLmsConnector(checked) as connector:
        info = await connector.site_info()
    write_env_value(env_file, "HSE_SMARTLMS_TOKEN", token)
    emit({"saved_to": str(env_file), "site": info.get("sitename"), "user": info.get("fullname")})
    return 0


async def password_login(settings: Settings, env_file: Path) -> int:
    """Логин и пароль вводит сам студент; пароль скрыт при вводе и не сохраняется."""
    print(
        "Вход в SmartLMS как в мобильном приложении Moodle. Пароль уходит только на "
        + settings.smartlms_base_url
        + " и не сохраняется; в .env запишется только ключ.",
        file=sys.stderr,
        flush=True,
    )
    username = (await asyncio.to_thread(input, "Логин ЕЛК (почта): ")).strip()
    password = await asyncio.to_thread(getpass.getpass, "Пароль (не отображается): ")
    try:
        async with httpx.AsyncClient(timeout=settings.smartlms_request_timeout) as client:
            token = await password_token(settings, client, username, password)
        del password
        return await save_checked_token(settings, env_file, token)
    except SmartLmsError as exc:
        emit({"error": exc.code})
        return 2


async def login(settings: Settings, env_file: Path) -> int:
    """Вход как у мобильного приложения: ЕЛК в браузере, ключ сразу в .env."""
    passport = secrets.token_hex(8)
    print(
        "1. Откройте в Chrome новую вкладку и нажмите F12 → вкладка Console.\n"
        "2. В этой же вкладке откройте ссылку и войдите через ЕЛК:\n"
        + launch_url(settings, passport)
        + "\n3. После входа страница останется пустой, а в Console появится строка\n"
        "   Failed to launch 'moodlemobile://token=…'. Скопируйте её целиком и вставьте сюда.",
        file=sys.stderr,
        flush=True,
    )
    value = await asyncio.to_thread(input, "Строка: ")
    try:
        return await save_checked_token(
            settings, env_file, token_from_launch(settings, passport, value)
        )
    except SmartLmsError as exc:
        emit({"error": exc.code})
        return 2


async def check(settings: Settings) -> int:
    if not configured(settings):
        emit({"error": "smartlms_not_configured"})
        return 2
    try:
        records, has_more, mode = await collect_deadlines(settings)
    except SmartLmsError as exc:
        emit({"error": exc.code})
        return 2
    emit(
        {
            "mode": mode,
            "deadlines": len(records),
            "has_more": has_more,
            "nearest": [
                {"title": r.attributes["facts"]["title"], "due": r.attributes["facts"]["due_raw"]}
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
    if args.command == "login":
        if args.browser:
            return await login(settings, Path(args.env_file))
        return await password_login(settings, Path(args.env_file))
    if args.command == "check":
        return await check(settings)
    if settings.database_url is None:
        emit({"error": "database_not_configured"})
        return 2
    if args.command == "worker" and not configured(settings):
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
    login_command = commands.add_parser("login")
    login_command.add_argument("--env-file", default=".env")
    login_command.add_argument(
        "--browser", action="store_true", help="вход по ссылке (в ВШЭ сейчас выключен)"
    )
    for name in ("check", "sync", "worker"):
        commands.add_parser(name)
    try:
        raise SystemExit(run_async(execute(parser.parse_args())))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
