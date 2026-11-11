import asyncio
import base64
import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.connectors.smartlms import (
    PAGE_SIZE,
    SmartLmsConnector,
    SmartLmsError,
    launch_url,
    password_token,
    token_from_launch,
)
from app.services.source_catalog import source_error_message

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def event(event_id, due, instance=None, **extra):
    return {
        "id": event_id,
        "name": f"Срок сдачи ДЗ {event_id}",
        "activityname": f"ДЗ {event_id}",
        "modulename": "assign",
        "instance": instance or event_id,
        "timesort": int(due.timestamp()),
        "course": {"id": 7, "fullname": "Алгоритмы"},
        "url": f"https://edu.hse.ru/mod/assign/view.php?id={event_id}",
        "description": "<p>Решить задачи <a href='https://edu.hse.ru/f.pdf'>условие</a></p>",
        "action": {"name": "Добавить ответ"},
        "overdue": False,
        **extra,
    }


def connector(handler, **overrides):
    settings = Settings(_env_file=None, smartlms_token="student-token", **overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SmartLmsConnector(settings, client)


def form(request):
    return dict(httpx.QueryParams(request.content.decode()))


def test_deadline_becomes_task_record_with_moscow_time_and_evidence():
    calls = []

    def handler(request):
        calls.append(form(request))
        return httpx.Response(
            200, json={"events": [event(1, datetime(2026, 11, 20, 20, 59, tzinfo=UTC))]}
        )

    records, has_more = asyncio.run(connector(handler).deadlines(NOW))
    assert not has_more
    record = records[0]
    facts = record.attributes["facts"]
    assert record.attributes["kind"] == "deadline"
    assert facts["title"] == "ДЗ 1" and facts["course"] == "Алгоритмы"
    assert facts["due_at"] == "2026-11-20T23:59:00+03:00"
    assert "Срок: 20.11.2026 23:59 (Europe/Moscow)" in record.text
    assert "Решить задачи условие" in record.text
    assert record.url.endswith("view.php?id=1")
    assert {"url": "https://edu.hse.ru/f.pdf", "text": "условие"} in record.links
    sent = calls[0]
    assert sent["wsfunction"] == "core_calendar_get_action_events_by_timesort"
    assert sent["wstoken"] == "student-token"
    assert "student-token" not in repr(record)


def test_moved_deadline_keeps_identity_so_change_is_detected():
    due = datetime(2026, 11, 20, 20, 59, tzinfo=UTC)
    moved = datetime(2026, 11, 27, 20, 59, tzinfo=UTC)
    first = connector(lambda r: httpx.Response(200, json={"events": [event(1, due, instance=5)]}))
    second = connector(
        lambda r: httpx.Response(200, json={"events": [event(9, moved, instance=5)]})
    )
    a = asyncio.run(first.deadlines(NOW))[0][0]
    b = asyncio.run(second.deadlines(NOW))[0][0]
    assert a.external_id == b.external_id == "deadline:assign:5"
    assert a.attributes["facts"]["due_raw"] != b.attributes["facts"]["due_raw"]


def test_pages_until_short_page_and_respects_limit():
    due = datetime(2026, 11, 20, tzinfo=UTC)
    pages = []

    def handler(request):
        params = form(request)
        pages.append(params.get("aftereventid"))
        start = int(params.get("aftereventid", 0))
        count = PAGE_SIZE if start == 0 else 3
        return httpx.Response(
            200, json={"events": [event(start + i + 1, due) for i in range(count)]}
        )

    records, has_more = asyncio.run(connector(handler).deadlines(NOW))
    assert pages == [None, str(PAGE_SIZE)]
    assert len(records) == PAGE_SIZE + 3 and not has_more
    limited, more = asyncio.run(connector(handler, smartlms_max_events=10).deadlines(NOW))
    assert len(limited) == 10 and more


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            httpx.Response(200, json={"exception": "x", "errorcode": "invalidtoken"}),
            "smartlms_auth_failed",
        ),
        (
            httpx.Response(200, json={"exception": "x", "errorcode": "servicenotavailable"}),
            "smartlms_servicenotavailable",
        ),
        (httpx.Response(503, text="down"), "smartlms_http_503"),
        (httpx.Response(200, text="<html>"), "smartlms_invalid_response"),
    ],
)
def test_upstream_errors_are_codes_without_details(response, code):
    with pytest.raises(SmartLmsError) as error:
        asyncio.run(connector(lambda r: response).deadlines(NOW))
    assert error.value.code == code


def test_missing_token_is_reported_to_user():
    with pytest.raises(SmartLmsError) as error:
        SmartLmsConnector(Settings(_env_file=None))
    assert error.value.code == "smartlms_not_configured"
    assert "app.smartlms login" in source_error_message("smartlms_auth_failed")


def test_mobile_launch_login_extracts_and_verifies_token():
    settings = Settings(_env_file=None)
    url = launch_url(settings, "abc123")
    assert url.startswith("https://edu.hse.ru/admin/tool/mobile/launch.php?")
    assert "service=moodle_mobile_app" in url and "passport=abc123" in url
    site_hash = hashlib.md5(b"https://edu.hse.ru" + b"abc123").hexdigest()
    encoded = base64.b64encode(f"{site_hash}:::tok42:::private".encode()).decode()
    console = f"Failed to launch 'moodlemobile://token={encoded}' because the scheme"
    assert token_from_launch(settings, "abc123", console) == "tok42"
    assert token_from_launch(settings, "abc123", encoded.rstrip("=")) == "tok42"
    with pytest.raises(SmartLmsError) as error:
        token_from_launch(settings, "other-passport", console)
    assert error.value.code == "smartlms_launch_mismatch"
    with pytest.raises(SmartLmsError) as error:
        token_from_launch(settings, "abc123", "garbage!!")
    assert error.value.code == "smartlms_bad_launch_token"


def test_password_login_returns_only_token_and_maps_errors():
    calls = []
    replies = [
        httpx.Response(200, json={"token": "tok", "privatetoken": "p"}),
        httpx.Response(200, json={"error": "Неверный логин", "errorcode": "invalidlogin"}),
        httpx.Response(200, json={"error": "x", "errorcode": "enablewsdescription"}),
    ]

    def handler(request):
        calls.append((request.url.path, form(request)))
        return replies.pop(0)

    settings = Settings(_env_file=None)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            token = await password_token(settings, client, "s@edu.hse.ru", "pw")
            codes = []
            for _ in range(2):
                with pytest.raises(SmartLmsError) as error:
                    await password_token(settings, client, "s@edu.hse.ru", "bad")
                codes.append(error.value.code)
            return token, codes

    token, codes = asyncio.run(run())
    assert token == "tok"
    assert codes == ["smartlms_invalid_login", "smartlms_enablewsdescription"]
    assert calls[0] == (
        "/login/token.php",
        {"username": "s@edu.hse.ru", "password": "pw", "service": "moodle_mobile_app"},
    )
    assert "логин или пароль" in source_error_message("smartlms_invalid_login")
