"""Interactive Telegram login; run directly in the operator's terminal."""

import argparse
import getpass
import json

from telethon import errors

from app.config import Settings
from app.connectors.telegram import TelegramError, telegram_client, telegram_error
from app.runtime import run as run_async


async def execute(command: str) -> int:
    settings = Settings()
    try:
        async with telegram_client(settings, login=command == "login") as client:
            if command == "login":
                if not await client.is_user_authorized():
                    phone = getpass.getpass("Номер телефона Telegram (ввод скрыт): ")
                    await client.send_code_request(phone)
                    try:
                        await client.sign_in(phone, getpass.getpass("Код Telegram: "))
                    except errors.SessionPasswordNeededError:
                        await client.sign_in(
                            password=getpass.getpass("Пароль двухэтапного входа: ")
                        )
                print("Telegram: сессия авторизована и сохранена на этом сервере.")
            else:
                async for dialog in client.iter_dialogs():
                    if dialog.is_group or dialog.is_channel:
                        username = getattr(dialog.entity, "username", None)
                        kind = "группа" if dialog.is_group else "канал"
                        print(
                            json.dumps(
                                {
                                    "id": dialog.id,
                                    "name": dialog.name,
                                    "type": kind,
                                    # Открытый канал можно читать без аккаунта: @username.
                                    "username": "@" + username if username else None,
                                },
                                ensure_ascii=False,
                            )
                        )
        return 0
    except (TelegramError, errors.RPCError, OSError, ValueError, TimeoutError) as exc:
        print(json.dumps({"error": telegram_error(exc).code}))
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("login", "dialogs"))
    args = parser.parse_args()
    try:
        raise SystemExit(run_async(execute(args.command)))
    except (KeyboardInterrupt, EOFError):
        print("Вход отменён.")


if __name__ == "__main__":
    main()
