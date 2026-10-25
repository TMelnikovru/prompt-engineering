import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas import Dashboard, Task
from app.config import Settings
from app.main import create_app


def client(demo_mode: bool = True) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, demo_mode=demo_mode)))


def test_preview_cannot_be_mistaken_for_connected_sources():
    response = client().get("/api/v1/dashboard")
    assert response.status_code == 200
    dashboard = Dashboard.model_validate(response.json())
    assert dashboard.meta.mode == "demo"
    assert dashboard.tasks
    assert {source.status for source in dashboard.sources} == {"not_connected"}
    assert all(source.last_synced_at is None for source in dashboard.sources)
    assert all(item.url is None for task in dashboard.tasks for item in task.evidence)
    assert any(task.due_date and not task.due_at for task in dashboard.tasks)
    assert any(not task.due_date and not task.due_at for task in dashboard.tasks)
    assert {block.task_id for block in dashboard.plan.blocks} <= {
        task.id for task in dashboard.tasks
    }
    assert sum(block.minutes for block in dashboard.plan.blocks) <= (
        dashboard.plan.available_minutes
    )


def test_preview_scenarios_exercise_empty_and_error_states():
    api = client()
    empty = api.get("/api/v1/dashboard?scenario=empty")
    assert empty.status_code == 200
    assert empty.json()["tasks"] == []
    assert empty.json()["plan"]["blocks"] == []
    failure = api.get("/api/v1/dashboard?scenario=error")
    assert failure.status_code == 503
    assert failure.json()["error"]["retryable"] is True
    assert api.get("/api/v1/dashboard?scenario=unknown").status_code == 422


def test_disabling_preview_removes_all_synthetic_tasks():
    api = client(demo_mode=False)
    payload = api.get("/api/v1/dashboard").json()
    assert payload["meta"]["mode"] == "unconfigured"
    assert payload["tasks"] == []
    assert payload["plan"]["blocks"] == []
    assert api.get("/api/v1/dashboard?scenario=empty").status_code == 400
    assert api.get("/api/v1/dashboard?scenario=error").status_code == 400


def test_task_rejects_invented_precision_and_naive_datetimes():
    base = {"id": "test", "title": "Задание", "course": "Курс", "priority": "low", "evidence": []}
    with pytest.raises(ValidationError):
        Task(**base, due_date="2026-11-01", due_at="2026-11-01T23:59:00+03:00")
    with pytest.raises(ValidationError):
        Task(**base, due_at="2026-11-01T23:59:00")


def test_site_and_its_local_assets_are_served_without_api_keys():
    api = client()
    assert api.get("/").status_code == 200
    for asset in ("/static/css/app.css", "/static/js/app.js", "/static/js/api.js"):
        assert api.get(asset).status_code == 200
    assert api.get("/api/v1/health").json()["status"] == "ok"


def test_page_assets_keep_https_when_proxy_uses_http_to_the_app():
    api = client()
    response = api.get("/", headers={"Host": "hse.example", "X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assets = [
        url for url in re.findall(r'(?:href|src)="([^"]+)"', response.text) if "/static/" in url
    ]
    assert assets
    for asset in assets:
        # Same-origin paths inherit the browser's HTTPS scheme even when proxy
        # headers are not trusted by the internal HTTP server.
        parsed = urlsplit(asset)
        assert not parsed.scheme
        assert not parsed.netloc
        assert api.get(asset).status_code == 200
