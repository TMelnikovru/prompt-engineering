import hashlib
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.base import DocumentRecord, SourceRecord
from app.connectors.wiki import WikiBatch, WikiConnector, WikiError
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


async def store_page(database: Database, page: DocumentRecord) -> str:
    return await store_record(
        database,
        page,
        raw_content=page.raw_content,
        revision_key=page.revision_key,
        content_hash=page.content_hash,
        attributes={
            **page.attributes,
            "links": page.links,
            "source_revision": page.revision_key,
            "published_at": page.published_at.isoformat() if page.published_at else None,
        },
    )


async def store_record(
    database: Database,
    page: SourceRecord,
    *,
    raw_content: str,
    revision_key: str,
    content_hash: str,
    attributes: dict,
) -> str:
    """Сохранить версию записи; то же содержимое не создаёт новую версию или документ."""
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
        revision_key = revision_key + ":" + content_hash
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.revision_key == revision_key,
            )
        )
        if version is None:
            attributes = dict(attributes)
            if document.current_version_id and attributes.get("kind") == "deadline":
                previous = await session.get(DocumentVersion, document.current_version_id)
                old_facts = previous.attributes.get("facts", {})
                new_facts = attributes.get("facts", {})
                if old_facts.get("due_raw") != new_facts.get("due_raw"):
                    attributes["deadline_change"] = {
                        "from": old_facts.get("due_raw"),
                        "to": new_facts.get("due_raw"),
                        "previous_evidence": previous.content_text,
                        "previous_fetched_at": previous.fetched_at.isoformat(),
                    }
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                revision_key=revision_key,
                content_hash=content_hash,
                raw_content=raw_content,
                content_text=page.text,
                fetched_at=page.fetched_at,
                attributes=attributes,
            )
            session.add(version)
            await session.flush()
        changed = document.current_version_id != version.id or document.is_active is False
        document.current_version_id = version.id
        document.title = page.title
        document.url = page.url
        document.last_seen_at = page.fetched_at
        document.is_active = True
        return "created" if created else "updated" if changed else "unchanged"


async def sync_wiki(database: Database, settings: Settings, batch: WikiBatch | None = None) -> dict:
    async with ingestion_lock(database, "hse:wiki:sync"):
        scope = {
            "seeds": settings.wiki_seeds,
            "max_pages": settings.wiki_max_pages,
            "max_depth": settings.wiki_max_depth,
        }
        scope = {
            **scope,
            **(batch.scope if batch else {}),
            "transport": "collector" if batch else "direct",
            "interval_seconds": settings.wiki_interval_seconds,
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
            if batch is not None:
                errors.extend(batch.errors)
                queue.clear()
                for page in batch.pages:
                    counts[await store_page(database, page)] += 1
                    visited_ids.add(page.external_id)
            else:
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
            run.scope = {**scope, "has_more": batch.has_more if batch else bool(queue)}
            source = await session.get(IngestionSource, "wiki")
            source.last_error = errors[0]["code"] if errors else None
            if status == "success":
                source.last_synced_at = finished_at
        return {
            "run_id": str(run_id),
            "status": status,
            **counts,
            "errors": errors,
            "has_more": batch.has_more if batch else bool(queue),
            "scope": scope,
        }
