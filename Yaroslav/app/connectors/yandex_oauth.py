"""OAuth Яндекса для чтения почты: вход по коду с устройства (device flow).

Студент сам входит через ЕЛК на странице Яндекса и вводит короткий код. Агент получает
токен только с правом `mail:imap_ro` и никогда не видит пароль ЕЛК.
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.connectors.mail import MailError


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_url: str
    interval: int
    expires_in: int


@dataclass
class OAuthToken:
    access_token: str
    expires_in: int | None


def _client_credentials(settings: Settings) -> dict[str, str]:
    if not settings.mail_oauth_client_id:
        raise MailError("mail_oauth_client_missing")
    data = {"client_id": settings.mail_oauth_client_id}
    if settings.mail_oauth_client_secret is not None:
        data["client_secret"] = settings.mail_oauth_client_secret.get_secret_value()
    return data


async def start_device_flow(settings: Settings, client: httpx.AsyncClient) -> DeviceCode:
    try:
        response = await client.post(
            settings.mail_oauth_base_url + "/device/code",
            data={
                "client_id": _client_credentials(settings)["client_id"],
                "device_name": "HSE Study Agent",
            },
        )
    except httpx.HTTPError:
        raise MailError("mail_oauth_network_error") from None
    if response.status_code != 200:
        raise MailError("mail_oauth_rejected")
    try:
        body = response.json()
        return DeviceCode(
            device_code=body["device_code"],
            user_code=body["user_code"],
            verification_url=body["verification_url"],
            interval=int(body.get("interval", 5)),
            expires_in=int(body.get("expires_in", 300)),
        )
    except (ValueError, KeyError, TypeError):
        raise MailError("mail_oauth_invalid_response") from None


async def wait_for_token(
    settings: Settings, client: httpx.AsyncClient, code: DeviceCode, sleep=asyncio.sleep
) -> OAuthToken:
    """Опрашивать /token, пока студент не подтвердит вход или код не истечёт."""
    deadline = time.monotonic() + code.expires_in
    interval = max(code.interval, 1)
    while time.monotonic() < deadline:
        await sleep(interval)
        try:
            response = await client.post(
                settings.mail_oauth_base_url + "/token",
                data={
                    "grant_type": "device_code",
                    "code": code.device_code,
                    **_client_credentials(settings),
                },
            )
            body = response.json()
        except httpx.HTTPError:
            raise MailError("mail_oauth_network_error") from None
        except ValueError:
            raise MailError("mail_oauth_invalid_response") from None
        if response.status_code == 200 and body.get("access_token"):
            expires = body.get("expires_in")
            return OAuthToken(body["access_token"], int(expires) if expires else None)
        error = body.get("error") if isinstance(body, dict) else None
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error in {"access_denied", "expired_token"}:
            raise MailError("mail_oauth_" + error)
        raise MailError("mail_oauth_rejected")
    raise MailError("mail_oauth_expired_token")


def write_env_value(path: Path, key: str, value: str) -> None:
    """Записать секрет в .env, не печатая его в терминал и не трогая другие строки."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[index] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def xoauth2_string(username: str, token: str) -> bytes:
    return f"user={username}\x01auth=Bearer {token}\x01\x01".encode()
