import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.connectors.smartlms import SmartLmsError
from app.connectors.smartlms_calendar import fetch_calendar, parse_ics
from app.services.source_catalog import source_error_message

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
URL = "https://edu.hse.ru/calendar/export_execute.php?userid=1&authtoken=secret&preset_what=all"

ICS = r"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:101@edu.hse.ru
SUMMARY:ДЗ 3 по алгоритмам: срок сдачи
DESCRIPTION:Решить задачи 1–5\, прислать PDF.\nУсловие в курсе
 .
CATEGORIES:Алгоритмы
DTSTART:20261120T205900Z
DTEND:20261120T205900Z
END:VEVENT
BEGIN:VEVENT
UID:102@edu.hse.ru
SUMMARY:Коллоквиум
CATEGORIES:Матанализ
DTSTART;VALUE=DATE:20261125
END:VEVENT
BEGIN:VEVENT
UID:99@edu.hse.ru
SUMMARY:Старое событие
DTSTART:20260901T090000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Без UID пропускается
DTSTART:20261121T090000Z
END:VEVENT
END:VCALENDAR
"""


def settings(**extra):
    return Settings(_env_file=None, smartlms_calendar_url=URL, **extra)


def client(response):
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))


def test_parse_unfolds_lines_and_unescapes_text():
    events = parse_ics(ICS)
    assert len(events) == 4
    assert events[0]["DESCRIPTION"][1].endswith("Условие в курсе.")
    assert events[1]["DTSTART"] == ({"VALUE": "DATE"}, "20261125")


def test_calendar_becomes_deadlines_with_exact_time_and_date_only():
    records, has_more = asyncio.run(
        fetch_calendar(settings(), client(httpx.Response(200, text=ICS)), NOW)
    )
    assert not has_more
    assert [r.attributes["facts"]["title"] for r in records] == [
        "ДЗ 3 по алгоритмам: срок сдачи",
        "Коллоквиум",
    ]
    exact, date_only = (r.attributes["facts"] for r in records)
    assert exact["due_at"] == "2026-11-20T23:59:00+03:00" and exact["course"] == "Алгоритмы"
    assert "Решить задачи 1–5, прислать PDF." in records[0].text
    # Дата без времени не превращается в 23:59.
    assert date_only == {
        "title": "Коллоквиум",
        "course": "Матанализ",
        "due_date": "2026-11-25",
        "due_raw": "2026-11-25",
    }
    assert "время не указано" in records[1].text
    assert all("secret" not in r.model_dump_json() for r in records)


def test_moved_deadline_keeps_identity():
    moved = ICS.replace("DTSTART:20261120T205900Z", "DTSTART:20261127T205900Z")
    first = asyncio.run(fetch_calendar(settings(), client(httpx.Response(200, text=ICS)), NOW))
    second = asyncio.run(fetch_calendar(settings(), client(httpx.Response(200, text=moved)), NOW))
    a = next(r for r in first[0] if r.title.startswith("ДЗ 3"))
    b = next(r for r in second[0] if r.title.startswith("ДЗ 3"))
    assert a.external_id == b.external_id
    assert a.attributes["facts"]["due_raw"] != b.attributes["facts"]["due_raw"]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, text="<html>Неверный ключ</html>"), "smartlms_calendar_invalid"),
        (httpx.Response(403, text="no"), "smartlms_http_403"),
    ],
)
def test_bad_feed_is_an_explicit_error(response, code):
    with pytest.raises(SmartLmsError) as error:
        asyncio.run(fetch_calendar(settings(), client(response), NOW))
    assert error.value.code == code
    assert source_error_message("smartlms_calendar_invalid").startswith("SmartLMS не отдал")


def test_calendar_url_must_point_to_smartlms_and_stays_secret():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, smartlms_calendar_url="https://evil.example/cal.ics")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, smartlms_calendar_url=URL.replace("https", "http"))
    assert "secret" not in repr(settings())
