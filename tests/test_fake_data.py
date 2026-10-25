from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.schemas import Dashboard
from app.config import Settings
from app.main import create_app
from app.services.fake_data import get_fake_dashboard


def test_explicit_fake_source_works_on_production_without_reading_real_sources():
    app = create_app(Settings(_env_file=None, demo_mode=False))
    # A real dashboard would try querying this object. Fake must return before DB access.
    app.state.database = object()
    client = TestClient(app)
    response = client.get("/api/v1/dashboard?data_source=fake&dsba_source_id=dsba:" + "a" * 32)
    assert response.status_code == 200
    dashboard = Dashboard.model_validate(response.json())
    assert dashboard.meta.mode == "demo"
    assert "вымышлены" in dashboard.meta.notice
    assert {s.id for s in dashboard.sources} == {"fake"}
    assert {e.source_id for t in dashboard.tasks for e in t.evidence} == {"fake"}
    assert all(t.id.startswith("fake-") for t in dashboard.tasks)
    assert any(t.status == "done" for t in dashboard.tasks)
    assert any(t.due_at is None and t.due_date for t in dashboard.tasks)
    assert any(not t.due_at and not t.due_date for t in dashboard.tasks)
    assert any(t.previous_due_at and t.deadline_changed for t in dashboard.tasks)
    for material in dashboard.materials:
        page = client.get(material.url)
        assert page.status_code == 200
        assert "Демонстрационный материал" in page.text
    assert client.get("/demo/materials/unknown").status_code == 404


def test_fake_plan_uses_budget_completion_and_day_boundary():
    settings = Settings(_env_file=None, demo_mode=False)
    now = datetime(2026, 10, 25, 14, tzinfo=ZoneInfo("Europe/Moscow"))
    first = get_fake_dashboard(settings, now=now)
    assert first.plan.blocks
    assert first.plan.unscheduled_task_ids
    completed = {first.plan.blocks[0].task_id}
    for minutes in (0, 30, 120, 720):
        dashboard = get_fake_dashboard(settings, minutes, completed, now=now)
        assert sum(b.minutes for b in dashboard.plan.blocks) <= minutes
        done = {t.id for t in dashboard.tasks if t.status == "done"}
        planned = {b.task_id for b in dashboard.plan.blocks}
        omitted = set(dashboard.plan.unscheduled_task_ids)
        assert not planned & done
        assert not omitted & done
        assert planned | omitted | done == {t.id for t in dashboard.tasks}
        assert completed <= done
    late = get_fake_dashboard(settings, 120, now=now.replace(hour=23, minute=35))
    for block in late.plan.blocks:
        assert block.starts_at >= now.replace(hour=23, minute=35)
        assert (
            block.starts_at + timedelta(minutes=block.minutes)
        ).date() <= now.date() + timedelta(days=1)
    assert sum(b.minutes for b in late.plan.blocks) <= 20


def test_fake_options_are_validated_and_do_not_enable_implicit_fake_data():
    client = TestClient(create_app(Settings(_env_file=None, demo_mode=False)))
    assert client.get("/api/v1/dashboard").json()["tasks"] == []
    for query in ("available_minutes=-1", "available_minutes=721", "data_source=unknown"):
        assert client.get("/api/v1/dashboard?data_source=fake&" + query).status_code == 422
    first = client.get("/api/v1/dashboard?data_source=fake").json()
    task_id = (
        first["plan"]["blocks"][0]["task_id"]
        if first["plan"]["blocks"]
        else first["tasks"][0]["id"]
    )
    updated = client.get(
        "/api/v1/dashboard",
        params={
            "data_source": "fake",
            "available_minutes": 30,
            "completed": task_id,
        },
    ).json()
    assert updated["plan"]["available_minutes"] == 30
    assert task_id not in {b["task_id"] for b in updated["plan"]["blocks"]}
