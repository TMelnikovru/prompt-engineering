"""Дедлайны SmartLMS из личной ссылки экспорта календаря Moodle (iCalendar).

Ссылку студент сам получает в «Календарь → Экспорт календаря → Получить адрес».
В ней личный ключ, поэтому она хранится только в серверном .env как секрет.
Пароль и сессия браузера не нужны.
"""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.connectors.base import DocumentRecord
from app.connectors.smartlms import SmartLmsError

MAX_ICS_BYTES = 5_000_000


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in {" ", "\t"} and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_ics(text: str) -> list[dict[str, tuple[dict[str, str], str]]]:
    """Минимальный разбор VEVENT: имя свойства → (параметры, значение)."""
    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *raw_params = head.split(";")
            params = dict(p.split("=", 1) for p in raw_params if "=" in p)
            current[name.upper()] = (params, value)
    return events


def parse_moment(params: dict[str, str], value: str, zone: ZoneInfo) -> datetime | date | None:
    """Дата без времени остаётся датой: не превращаем её в вымышленные 23:59."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date()
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(zone)
        local = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tz = ZoneInfo(params["TZID"]) if "TZID" in params else zone
        return local.replace(tzinfo=tz).astimezone(zone)
    except (ValueError, KeyError):
        return None


def event_record(event: dict, settings: Settings, fetched_at: datetime) -> DocumentRecord | None:
    zone = ZoneInfo(settings.timezone)
    uid = event.get("UID", ({}, ""))[1].strip()
    due = parse_moment(*event.get("DTSTART", ({}, "")), zone) if "DTSTART" in event else None
    if not uid or due is None:
        return None
    title = _unescape(event.get("SUMMARY", ({}, "Событие"))[1]).strip() or "Событие"
    course = _unescape(event.get("CATEGORIES", ({}, ""))[1]).strip() or "SmartLMS"
    description = _unescape(event.get("DESCRIPTION", ({}, ""))[1]).strip()
    url = event.get("URL", ({}, ""))[1].strip() or settings.smartlms_base_url
    if isinstance(due, datetime):
        due_text = f"{due.strftime('%d.%m.%Y %H:%M')} ({settings.timezone})"
        facts = {"due_at": due.isoformat(), "due_raw": due.isoformat()}
    else:
        due_text = due.strftime("%d.%m.%Y") + ", время не указано"
        facts = {"due_date": due.isoformat(), "due_raw": due.isoformat()}
    text = f"Курс: {course}\nСобытие: {title}\nСрок: {due_text}"
    if description:
        text += "\n\n" + description[:20_000]
    return DocumentRecord(
        source_id="smartlms",
        # UID события Moodle не меняется при переносе срока: изменение попадёт в историю.
        external_id="deadline:ical:" + hashlib.sha256(uid.encode()).hexdigest()[:40],
        title=f"{title} — {course}"[:500],
        text=text,
        url=url,
        published_at=None,
        fetched_at=fetched_at,
        content_type="text/calendar",
        raw_content=text,
        revision_key="ical",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        links=[{"url": url, "text": title}] if url.startswith("https://") else [],
        attributes={
            "kind": "deadline",
            "course": course,
            "facts": {"title": title, "course": course, **facts},
        },
    )


async def fetch_calendar(
    settings: Settings, client: httpx.AsyncClient | None = None, now: datetime | None = None
) -> tuple[list[DocumentRecord], bool]:
    if settings.smartlms_calendar_url is None:
        raise SmartLmsError("smartlms_not_configured")
    url = settings.smartlms_calendar_url.get_secret_value()
    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=settings.smartlms_request_timeout,
        follow_redirects=False,
        headers={"User-Agent": "HSEStudyAgent/0.1 (student calendar feed)"},
    )
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        raise SmartLmsError("smartlms_timeout") from None
    except httpx.HTTPError:
        raise SmartLmsError("smartlms_network_error") from None
    finally:
        if own_client:
            await client.aclose()
    if response.status_code != 200:
        raise SmartLmsError(f"smartlms_http_{response.status_code}")
    if len(response.content) > MAX_ICS_BYTES:
        raise SmartLmsError("smartlms_calendar_too_large")
    body = response.text
    if "BEGIN:VCALENDAR" not in body:
        # Неверный или отозванный ключ: Moodle отвечает HTML-страницей ошибки.
        raise SmartLmsError("smartlms_calendar_invalid")
    now = now or datetime.now(UTC)
    zone = ZoneInfo(settings.timezone)
    oldest = (now - timedelta(days=settings.smartlms_lookback_days)).astimezone(zone)
    records = []
    for event in parse_ics(body):
        record = event_record(event, settings, now)
        if record is None:
            continue
        facts = record.attributes["facts"]
        moment = facts.get("due_at") or facts.get("due_date")
        if moment[:10] < oldest.date().isoformat():
            continue
        records.append(record)
    records.sort(key=lambda r: r.attributes["facts"]["due_raw"])
    limited = records[: settings.smartlms_max_events]
    return limited, len(records) > len(limited)
