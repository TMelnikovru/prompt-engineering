"""SmartLMS ВШЭ (edu.hse.ru, Moodle) через официальный веб-сервис мобильного приложения.

Студент сам берёт свой ключ на странице «Ключи безопасности» после входа через ЕЛК.
Агент только читает: дедлайны из календаря и курсы, ничего не отправляет и не отмечает.
"""

import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.connectors.base import DocumentRecord
from app.connectors.wiki import clean_html

PAGE_SIZE = 50


class SmartLmsError(Exception):
    """Безопасный код ошибки без токена и текста ответа."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def launch_url(settings: Settings, passport: str) -> str:
    """Официальный вход мобильного приложения Moodle: студент входит через ЕЛК сам."""
    query = urlencode(
        {"service": "moodle_mobile_app", "passport": passport, "urlscheme": "moodlemobile"}
    )
    return settings.smartlms_base_url + "/admin/tool/mobile/launch.php?" + query


def token_from_launch(settings: Settings, passport: str, value: str) -> str:
    """Достать ключ из адреса moodlemobile://token=<base64>, которым Moodle завершает вход.

    Первая часть — md5(адрес сайта + passport): так проверяем, что ответ пришёл
    именно на наш запрос и от нужного сайта.
    """
    match = re.search(r"token=([A-Za-z0-9+/=_-]+)", value.strip())
    encoded = match.group(1) if match else value.strip()
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise SmartLmsError("smartlms_bad_launch_token") from None
    parts = decoded.split(":::")
    if len(parts) < 2 or not parts[1]:
        raise SmartLmsError("smartlms_bad_launch_token")
    expected = hashlib.md5((settings.smartlms_base_url + passport).encode()).hexdigest()
    if parts[0] != expected:
        raise SmartLmsError("smartlms_launch_mismatch")
    return parts[1]


async def password_token(
    settings: Settings, client: httpx.AsyncClient, username: str, password: str
) -> str:
    """Вход официального мобильного приложения по логину и паролю (login/token.php).

    Пароль уходит только на SmartLMS по HTTPS и нигде не сохраняется: вызывающий
    код получает только ключ.
    """
    try:
        response = await client.post(
            settings.smartlms_base_url + "/login/token.php",
            data={"username": username, "password": password, "service": "moodle_mobile_app"},
        )
    except httpx.TimeoutException:
        raise SmartLmsError("smartlms_timeout") from None
    except httpx.HTTPError:
        raise SmartLmsError("smartlms_network_error") from None
    try:
        body = response.json()
    except ValueError:
        raise SmartLmsError("smartlms_invalid_response") from None
    if isinstance(body, dict) and body.get("token"):
        return body["token"]
    code = body.get("errorcode") if isinstance(body, dict) else None
    if code == "invalidlogin":
        raise SmartLmsError("smartlms_invalid_login")
    raise SmartLmsError("smartlms_" + str(code or "login_failed")[:40])


class SmartLmsConnector:
    source_id = "smartlms"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if settings.smartlms_token is None:
            raise SmartLmsError("smartlms_not_configured")
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.smartlms_request_timeout,
            follow_redirects=False,
            headers={"User-Agent": "HSEStudyAgent/0.1 (student-authorized read-only)"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def call(self, function: str, **params: Any) -> Any:
        data = {
            "wstoken": self.settings.smartlms_token.get_secret_value(),
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **{key: str(value) for key, value in params.items()},
        }
        try:
            response = await self.client.post(
                self.settings.smartlms_base_url + "/webservice/rest/server.php", data=data
            )
        except httpx.TimeoutException:
            raise SmartLmsError("smartlms_timeout") from None
        except httpx.HTTPError:
            raise SmartLmsError("smartlms_network_error") from None
        if response.status_code != 200:
            raise SmartLmsError(f"smartlms_http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise SmartLmsError("smartlms_invalid_response") from None
        if isinstance(body, dict) and body.get("exception"):
            code = body.get("errorcode") or "error"
            if code in {"invalidtoken", "accessexception"}:
                raise SmartLmsError("smartlms_auth_failed")
            raise SmartLmsError("smartlms_" + str(code)[:40])
        return body

    async def site_info(self) -> dict:
        return await self.call("core_webservice_get_site_info")

    async def deadlines(self, now: datetime | None = None) -> tuple[list[DocumentRecord], bool]:
        """Календарь действий: задания, тесты и прочее со сроком. Сданное Moodle убирает сам."""
        now = now or datetime.now(UTC)
        start = int((now - timedelta(days=self.settings.smartlms_lookback_days)).timestamp())
        end = int((now + timedelta(days=self.settings.smartlms_lookahead_days)).timestamp())
        records: list[DocumentRecord] = []
        after: int | None = None
        while len(records) < self.settings.smartlms_max_events:
            params: dict[str, Any] = {
                "timesortfrom": start,
                "timesortto": end,
                "limitnum": PAGE_SIZE,
            }
            if after is not None:
                params["aftereventid"] = after
            body = await self.call("core_calendar_get_action_events_by_timesort", **params)
            events = body.get("events", []) if isinstance(body, dict) else []
            records.extend(self.event_record(event, now) for event in events)
            if len(events) < PAGE_SIZE:
                return records, False
            after = events[-1]["id"]
        return records[: self.settings.smartlms_max_events], True

    def event_record(self, event: dict, fetched_at: datetime) -> DocumentRecord:
        zone = ZoneInfo(self.settings.timezone)
        due = datetime.fromtimestamp(int(event["timesort"]), tz=UTC).astimezone(zone)
        course = (event.get("course") or {}).get("fullname") or "Курс SmartLMS"
        title = event.get("activityname") or event.get("name") or "Задание"
        description, links = clean_html(event.get("description") or "")
        url = event.get("url") or self.settings.smartlms_base_url
        action = (event.get("action") or {}).get("name")
        lines = [
            f"Курс: {course}",
            f"Задание: {title}",
            f"Срок: {due.strftime('%d.%m.%Y %H:%M')} ({self.settings.timezone})",
        ]
        if event.get("modulename"):
            lines.append("Тип: " + str(event["modulename"]))
        if action:
            lines.append("Действие: " + str(action))
        if event.get("overdue"):
            lines.append("Срок прошёл")
        text = "\n".join(lines) + ("\n\n" + description if description else "")
        facts = {
            "title": title,
            "course": course,
            "due_at": due.isoformat(),
            "due_raw": due.isoformat(),
            "module": event.get("modulename"),
            "overdue": bool(event.get("overdue")),
        }
        # Ключ — модуль и экземпляр активности: перенос срока обновит ту же запись.
        identity = f"{event.get('modulename')}:{event.get('instance') or event['id']}"
        return DocumentRecord(
            source_id=self.source_id,
            external_id="deadline:" + identity,
            title=f"{title} — {course}"[:500],
            text=text,
            url=url,
            published_at=None,
            fetched_at=fetched_at,
            content_type="text/plain",
            raw_content=text,
            revision_key="event",
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            links=[
                {"url": href, "text": label}
                for href, label in [(url, title), *links]
                if href.startswith(("http://", "https://"))
            ][:50],
            attributes={"kind": "deadline", "course": course, "facts": facts},
        )
