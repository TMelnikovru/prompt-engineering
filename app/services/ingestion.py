import hashlib
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.wiki import WikiConnector, WikiError, WikiPage
from app.db.models import DocumentVersion, IngestionRun, IngestionSource, SourceDocument
from app.db.session import Database


class SyncBusy(Exception):
    pass


@asynccontextmanager
async def ingestion_lock(database: Database, name: str):
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    async with database.lock_engine.connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        await connection.commit()
        if not acquired:
            raise SyncBusy
        try:
            yield
        finally:
            with suppress(SQLAlchemyError):
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                await connection.commit()


async def store_page(database: Database, page: WikiPage) -> str:
    async with database.session() as session, session.begin():
        document = await session.scalar(
            select(SourceDocument)
            .where(
                SourceDocument.source_id == page.source_id,
                SourceDocument.external_id == page.external_id,
            )
            .with_for_update()
        )
        created = document is None
        if created:
            document = SourceDocument(
                id=uuid4(),
                source_id=page.source_id,
                external_id=page.external_id,
                title=page.title,
                url=page.url,
                last_seen_at=page.fetched_at,
            )
            session.add(document)
            await session.flush()
        revision_key = page.revision_key + ":" + page.content_hash
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.revision_key == revision_key,
            )
        )
        if version is None:
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                revision_key=revision_key,
                content_hash=page.content_hash,
                raw_content=page.raw_content,
                content_text=page.text,
                fetched_at=page.fetched_at,
                attributes={"links": page.links, "source_revision": page.revision_key},
            )
            session.add(version)
            await session.flush()
        changed = document.current_version_id != version.id
        document.current_version_id = version.id
        document.title = page.title
        document.url = page.url
        document.last_seen_at = page.fetched_at
        return "created" if created else "updated" if changed else "unchanged"


async def sync_wiki(database: Database, settings: Settings) -> dict:
    async with ingestion_lock(database, "hse:wiki:sync"):
        scope = {
            "seeds": settings.wiki_seeds,
            "max_pages": settings.wiki_max_pages,
            "max_depth": settings.wiki_max_depth,
        }
        run_id = uuid4()
        async with database.session() as session, session.begin():
            await session.execute(
                insert(IngestionSource)
                .values(
                    id="wiki",
                    kind="wiki",
                    name="Вики ФКН",
                    base_url=settings.wiki_api_url,
                    config=scope,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"base_url": settings.wiki_api_url, "config": scope},
                )
            )
            # Полученный advisory lock означает, что предыдущего активного процесса уже нет.
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.source_id == "wiki", IngestionRun.status == "running")
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
            session.add(IngestionRun(id=run_id, source_id="wiki", status="running", scope=scope))

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        errors = []
        queue = deque((title, 0) for title in settings.wiki_seeds)
        visited_titles, visited_ids = set(), set()
        attempts = 0
        try:
            async with WikiConnector(settings) as connector:
                while queue and len(visited_ids) < settings.wiki_max_pages:
                    title, depth = queue.popleft()
                    if title in visited_titles:
                        continue
                    visited_titles.add(title)
                    attempts += 1
                    if attempts > settings.wiki_max_pages * 3:
                        break
                    try:
                        page = await connector.fetch_page(title)
                        if page.external_id in visited_ids:
                            continue
                        counts[await store_page(database, page)] += 1
                        visited_ids.add(page.external_id)
                        if depth < settings.wiki_max_depth:
                            queue.extend((item, depth + 1) for item in page.linked_titles)
                    except WikiError as exc:
                        errors.append({"title": title, "code": exc.code})
                    if settings.wiki_request_delay:
                        import asyncio

                        await asyncio.sleep(settings.wiki_request_delay)
        except BaseException:
            # Cancellation and DB errors must not make an unfinished run look successful.
            async with database.session() as session, session.begin():
                run = await session.get(IngestionRun, run_id)
                run.status = "interrupted"
                run.finished_at = datetime.now(UTC)
            raise

        status = ("partial" if visited_ids else "failed") if errors else "success"
        if not visited_ids:
            status = "failed"
        finished_at = datetime.now(UTC)
        async with database.session() as session, session.begin():
            run = await session.get(IngestionRun, run_id)
            run.status, run.finished_at = status, finished_at
            run.created_count = counts["created"]
            run.updated_count = counts["updated"]
            run.unchanged_count = counts["unchanged"]
            run.errors = errors[:50]
            run.scope = {**scope, "has_more": bool(queue)}
            source = await session.get(IngestionSource, "wiki")
            source.last_error = errors[0]["code"] if errors else None
            if status == "success":
                source.last_synced_at = finished_at
        return {
            "run_id": str(run_id),
            "status": status,
            **counts,
            "errors": errors,
            "has_more": bool(queue),
            "scope": scope,
        }
