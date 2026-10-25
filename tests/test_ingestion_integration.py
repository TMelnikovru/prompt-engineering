import asyncio
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import Settings
from app.db.models import AiArtifact, DocumentVersion, SourceDocument
from app.db.session import Database
from app.db.url import postgresql_url
from app.main import create_app
from app.services.analysis import AiError, analyze_document, summarize
from app.services.ingestion import SyncBusy, ingestion_lock, sync_wiki
from app.services.library import list_sources

pytestmark = pytest.mark.integration


@pytest.fixture
def ingestion_url():
    value = os.getenv("HSE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Нужна отдельная PostgreSQL БД через HSE_TEST_DATABASE_URL")
    if not postgresql_url(value).database.endswith("_test"):
        pytest.fail("Разрешена только отдельная БД с суффиксом _test")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "HSE_DATABASE_URL": value},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return value


def test_complete_ingestion_pipeline_versions_cache_errors_and_api(ingestion_url, upstreams):
    settings = Settings(
        _env_file=None,
        database_url=ingestion_url,
        demo_mode=False,
        ingestion_token="operator-simulation",
        wiki_api_url=upstreams["fkn-wiki"] + "/api.php",
        wiki_request_delay=0,
        wiki_max_pages=2,
        openai_api_key="simulation-only",
        openai_base_url=upstreams["openai"] + "/v1",
        extraction_model="simulation-extraction",
        summary_model="simulation-summary",
    )

    async def run():
        database = Database(settings)
        try:
            async with database.session() as session, session.begin():
                await session.execute(
                    text(
                        "TRUNCATE ai_artifacts, document_versions, source_documents, "
                        "ingestion_runs, ingestion_sources CASCADE"
                    )
                )
            async with ingestion_lock(database, "hse:wiki:sync"):
                with pytest.raises(SyncBusy):
                    await sync_wiki(database, settings)
            first = await sync_wiki(database, settings)
            assert first["status"] == "success" and first["created"] == 2
            repeated = await sync_wiki(database, settings)
            assert repeated["unchanged"] == 2 and repeated["created"] == 0
            async with database.session() as session:
                assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 2
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
                course = await session.scalar(
                    select(SourceDocument).where(SourceDocument.external_id == "2")
                )
                doc_id = course.id
            async with httpx.AsyncClient() as client:
                await client.post(upstreams["fkn-wiki"] + "/__control", json={"revision": 2})
            changed = await sync_wiki(database, settings)
            assert changed["updated"] == 1 and changed["unchanged"] == 1
            extracted = await analyze_document(database, settings, doc_id)
            assert extracted["cached"] is False
            assert "29 октября" in extracted["payload"]["chunks"][0]["items"][0]["due_text"]
            cached = await analyze_document(database, settings, doc_id)
            assert cached["cached"] is True
            assert cached["id"] == extracted["id"]
            summary = await summarize(database, settings)
            assert summary["payload"]["highlights"][0]["document_ids"] == [str(doc_id)]
            assert (await summarize(database, settings))["cached"] is True
            async with httpx.AsyncClient() as client:
                assert (await client.get(upstreams["openai"] + "/__stats")).json()["requests"] == 2
                # Новая revision с тем же текстом не должна повторно вызывать модель.
                await client.post(upstreams["fkn-wiki"] + "/__control", json={"revision": 3})
            await sync_wiki(database, settings)
            assert (await analyze_document(database, settings, doc_id))["cached"] is True
            async with httpx.AsyncClient() as client:
                assert (await client.get(upstreams["openai"] + "/__stats")).json()["requests"] == 2
                await client.post(upstreams["fkn-wiki"] + "/__control", json={"revision": 4})
                await client.post(upstreams["openai"] + "/__control", json={"mode": "bad-evidence"})
            await sync_wiki(database, settings)
            with pytest.raises(AiError, match="unsupported_evidence"):
                await analyze_document(database, settings, doc_id)
            async with database.session() as session:
                assert await session.scalar(select(func.count()).select_from(AiArtifact)) == 2
                assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 2
            async with httpx.AsyncClient() as client:
                await client.post(upstreams["fkn-wiki"] + "/__control", json={"mode": "error"})
            failed = await sync_wiki(database, settings)
            assert failed["status"] == "failed"
            sources = await list_sources(database)
            assert sources[0]["status"] == "stale"
            assert sources[0]["last_synced_at"] is not None
            return doc_id
        finally:
            await database.close()

    doc_id = asyncio.run(run())
    with TestClient(create_app(settings)) as api:
        listed = api.get("/api/v1/documents?limit=1").json()
        assert listed["total"] == 2 and len(listed["items"]) == 1
        detail = api.get("/api/v1/documents/" + str(doc_id)).json()
        assert detail["version_count"] == 4
        assert "28 октября" in detail["content_text"]
        assert api.get("/api/v1/dashboard").json()["meta"]["mode"] == "live"
        assert api.post("/api/v1/integrations/wiki/sync").status_code == 401
        assert api.get("/api/v1/documents?limit=0").status_code == 422
