import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.config import Settings
from app.connectors.dsba import DsbaSelection
from app.db.models import DocumentVersion, IngestionRun, SourceDocument
from app.db.session import Database
from app.db.url import postgresql_url
from app.ingest import analyze_pending
from app.main import create_app
from app.services.dsba import refresh_dsba_profiles, sync_dsba
from app.services.ingestion import ingestion_lock

pytestmark = pytest.mark.integration


@pytest.fixture
def dsba_database_url():
    value = os.getenv("HSE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Нужна отдельная PostgreSQL БД через HSE_TEST_DATABASE_URL")
    if not postgresql_url(value).database.endswith("_test"):
        pytest.fail("Разрешена только отдельная тестовая PostgreSQL БД")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "HSE_DATABASE_URL": value},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return value


def test_frontend_selection_versions_outage_deletions_and_worker(dsba_database_url, upstreams):
    settings = Settings(
        _env_file=None,
        database_url=dsba_database_url,
        demo_mode=False,
        dsba_base_url=upstreams["dsba"],
        dsba_max_profiles=2,
    )
    first = DsbaSelection(program="dsba", year=1, group="261")
    second = DsbaSelection(program="dsba", year=1, group="262")

    def db_run(action):
        async def run():
            database = Database(settings)
            try:
                return await action(database)
            finally:
                await database.close()

        return asyncio.run(run())

    async def reset(database):
        async with database.session() as session, session.begin():
            await session.execute(
                text(
                    "TRUNCATE ai_artifacts, document_versions, source_documents, "
                    "ingestion_runs, ingestion_sources CASCADE"
                )
            )

    db_run(reset)

    def control(**values):
        response = httpx.post(upstreams["dsba"] + "/__control", json=values)
        response.raise_for_status()

    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v1/integrations/dsba/catalog").json()["programs"][0]["id"] == "dsba"
        invalid = {**first.model_dump(), "group": "not-in-catalog"}
        assert api.post("/api/v1/integrations/dsba/sync", json=invalid).status_code == 400
        injected = {**first.model_dump(), "url": "http://localhost/private"}
        assert api.post("/api/v1/integrations/dsba/sync", json=injected).status_code == 422

        initial = api.post("/api/v1/integrations/dsba/sync", json=first.model_dump())
        assert initial.status_code == 200
        assert initial.json()["source_id"] == first.source_id
        assert initial.json()["created"] == 4
        assert api.post("/api/v1/integrations/dsba/sync", json=first.model_dump()).json()["cached"]
        assert (
            api.post("/api/v1/integrations/dsba/sync", json=second.model_dump()).status_code == 200
        )
        assert (
            api.post(
                "/api/v1/integrations/dsba/sync",
                json={
                    **first.model_dump(),
                    "sub": "1",
                },
            ).status_code
            == 429
        )

        def dashboard(selection):
            return api.get(
                "/api/v1/dashboard", params={"dsba_source_id": selection.source_id}
            ).json()

        assert api.get("/api/v1/dashboard").json()["tasks"] == []
        tasks = dashboard(first)["tasks"]
        assert len(tasks) == 3
        assert all(
            t["status"] == "todo" for t in tasks
        )  # Public source status isn't personal completion.
        dated = next(t for t in tasks if t["title"] == "Учебная контрольная")
        assert dated["due_date"] == "2026-11-08" and dated["due_at"] is None
        unknown = next(t for t in tasks if t["title"] == "Срок пока неизвестен")
        assert unknown["due_at"] is None and unknown["due_date"] is None
        assignment = next(t for t in tasks if t["title"] == "Учебная лабораторная 2")
        task_id = assignment["id"]
        assert assignment["evidence"][0]["fetched_at"]
        assert assignment["evidence"][0]["external_id"] == "deadline:31"

        control(revision=2)
        changed = db_run(lambda db: sync_dsba(db, settings, first, force=True))
        assert changed["updated"] == 1
        changed_task = next(t for t in dashboard(first)["tasks"] if t["id"] == task_id)
        assert "2026-10-29" in changed_task["due_at"]
        assert "2026-10-28" in changed_task["deadline_changed"]
        assert len(changed_task["evidence"]) == 2
        assert db_run(lambda db: sync_dsba(db, settings, first, force=True))["unchanged"] == 4
        assert len(dashboard(second)["tasks"]) == 3

        control(mode="deadline-error")
        partial = db_run(lambda db: sync_dsba(db, settings, first, force=True))
        assert partial["status"] == "partial"
        assert len(dashboard(first)["tasks"]) == 3
        source = next(s for s in api.get("/api/v1/sources").json() if s["id"] == first.source_id)
        assert source["status"] == "partial"
        last_success = source["last_synced_at"]

        control(mode="all-error")
        failed = db_run(lambda db: sync_dsba(db, settings, first, force=True))
        assert failed["status"] == "failed"
        source = next(s for s in api.get("/api/v1/sources").json() if s["id"] == first.source_id)
        assert source["status"] == "stale" and source["last_synced_at"] == last_success
        assert len(dashboard(first)["tasks"]) == 3

        control(mode="deleted")
        assert db_run(lambda db: sync_dsba(db, settings, first, force=True))["status"] == "success"
        assert dashboard(first)["tasks"] == []
        assert len(dashboard(second)["tasks"]) == 3
        assert (
            api.get("/api/v1/documents", params={"source_id": first.source_id}).json()["total"] == 1
        )
        assert api.get("/api/v1/documents/" + task_id).json()["version_count"] == 2

        async def check_versions_and_schedule(database):
            async with database.session() as session, session.begin():
                assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 8
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 9
                await session.execute(
                    update(IngestionRun)
                    .where(
                        IngestionRun.source_id == first.source_id,
                    )
                    .values(started_at=datetime.now(UTC) - timedelta(hours=2))
                )
            assert (await analyze_pending(database, settings, 10))["pending_before"] == 0
            return await refresh_dsba_profiles(database, settings)

        control(mode="ok")
        refreshed = db_run(check_versions_and_schedule)
        assert refreshed == [{"source_id": first.source_id, "status": "success"}]
        assert len(dashboard(first)["tasks"]) == 3

        async def locked(database):
            async with ingestion_lock(database, "hse:dsba:sync"):
                return api.post(
                    "/api/v1/integrations/dsba/sync", json=first.model_dump()
                ).status_code

        assert db_run(locked) == 409
