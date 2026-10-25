import asyncio
from argparse import Namespace
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from test_telegram import TelegramTransport, message

from app.config import Settings
from app.connectors.telegram import TelegramError
from app.db.models import AiArtifact, DocumentVersion, IngestionSource, SourceDocument
from app.db.session import Database
from app.ingest import telegram_cycle
from app.main import create_app
from app.services.analysis import AiError
from app.services.library import list_sources
from app.services.telegram import latest_telegram_digest, summarize_telegram, sync_telegram

pytestmark = pytest.mark.integration


def test_telegram_persistence_restart_edits_context_and_isolated_chat_failure(
    ingestion_url,
    upstreams,
    monkeypatch,
):
    settings = Settings(
        _env_file=None,
        database_url=ingestion_url,
        ingestion_token="test-operator",
        telegram_chats=["@selected"],
        telegram_batch_size=2,
        telegram_edit_scan_limit=10,
        openai_api_key="simulation-only",
        openai_base_url=upstreams["openai"] + "/v1",
        summary_model="simulation-summary",
    )
    transport = TelegramTransport(
        [
            message(1, "Домашняя работа: сдать в пятницу"),
            message(2, "Где лежат материалы к семинару?"),
            message(3, "Материалы закреплены в чате"),
        ]
    )

    @asynccontextmanager
    async def connected(_settings):
        yield transport

    monkeypatch.setattr("app.services.telegram.telegram_client", connected)

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
            first = await sync_telegram(database, settings)
            assert first["created"] == 2 and first["status"] == "partial"
            assert first["has_more"]
            await database.close()
            database = Database(settings)  # Durable cursor survives process restart.
            second = await sync_telegram(database, settings)
            assert second["created"] == 1 and second["status"] == "success"
            repeated = await sync_telegram(database, settings)
            assert repeated["created"] == 0 and repeated["unchanged"] == 3
            async with database.session() as session:
                assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 3
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 3
                document = await session.scalar(
                    select(SourceDocument).where(SourceDocument.external_id.endswith(":1"))
                )
                document_id = document.id
            digest = await summarize_telegram(database, settings)
            assert digest["cached"] is False
            assert digest["payload"]["assignments"][0]["evidence_quote"].endswith("пятницу")
            assert (await summarize_telegram(database, settings))["cached"] is True
            assert (await latest_telegram_digest(database, settings))["stale"] is False
            transport.messages[0].message = "Домашняя работа: срок перенесён на понедельник"
            transport.messages[0].edit_date = datetime.now(UTC)
            changed = await sync_telegram(database, settings)
            assert changed["updated"] == 1
            assert (await latest_telegram_digest(database, settings))["stale"] is True
            refreshed = await summarize_telegram(database, settings)
            assert refreshed["cached"] is False
            assert "понедельник" in refreshed["payload"]["assignments"][0]["evidence_quote"]

            settings.telegram_chats = ["@unavailable", "@selected"]
            partial = await sync_telegram(database, settings)
            assert partial["status"] == "partial" and partial["unchanged"] == 3
            assert partial["errors"][0]["chat"] == "@unavailable"
            assert (await list_sources(database))[0]["status"] == "partial"
            settings.telegram_chats = ["@selected"]
            async with httpx.AsyncClient() as client:
                await client.post(upstreams["openai"] + "/__control", json={"mode": "rate-limit"})
            transport.messages.append(message(4, "Встречаемся на семинаре"))
            failed_ai = await telegram_cycle(database, settings, Namespace(summarize=True))
            assert failed_ai["sync"]["created"] == 1
            assert failed_ai["summary"]["error"] == "openai_http_429"
            async with httpx.AsyncClient() as client:
                await client.post(upstreams["openai"] + "/__control", json={"mode": "bad-evidence"})
            with pytest.raises(AiError, match="unsupported_telegram_evidence"):
                await summarize_telegram(database, settings)
            async with httpx.AsyncClient() as client:
                await client.post(upstreams["openai"] + "/__control", json={"mode": "ok"})

            @asynccontextmanager
            async def unavailable(_settings):
                raise TelegramError("telegram_authorization_required")
                yield

            monkeypatch.setattr("app.services.telegram.telegram_client", unavailable)
            recovered_ai = await telegram_cycle(database, settings, Namespace(summarize=True))
            assert recovered_ai["sync"]["status"] == "failed"
            assert recovered_ai["summary"]["cached"] is False
            sources = await list_sources(database)
            assert sources[0]["status"] == "stale" and sources[0]["last_synced_at"] is not None
            async with database.session() as session:
                assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 4
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 5
                assert await session.scalar(select(func.count()).select_from(AiArtifact)) == 3
                source = await session.get(IngestionSource, "telegram")
                assert source.config["cursors"]["@selected"]["last_id"] == 4
            return document_id
        finally:
            await database.close()

    document_id = asyncio.run(run())
    with TestClient(create_app(settings)) as api:
        headers = {"X-Ingestion-Token": "test-operator"}
        assert api.get("/api/v1/documents?source_id=telegram").status_code == 401
        assert api.get("/api/v1/documents/" + str(document_id)).status_code == 401
        public = [source["id"] for source in api.get("/api/v1/sources").json()]
        assert "telegram" not in public
        private = [s["id"] for s in api.get("/api/v1/sources", headers=headers).json()]
        assert "telegram" in private
        documents = api.get("/api/v1/documents?source_id=telegram", headers=headers).json()
        assert documents["total"] == 4
        detail = api.get("/api/v1/documents/" + str(document_id), headers=headers).json()
        assert detail["version_count"] == 2 and "понедельник" in detail["content_text"]
        summary = api.get("/api/v1/knowledge/telegram", headers=headers).json()
        assert summary["summary"]["scope"]["message_count"] == 4
        assert summary["stale"] is False
