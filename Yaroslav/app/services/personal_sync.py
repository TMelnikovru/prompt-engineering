"""Общая запись пакета личного источника (почта, SmartLMS) в схему документов и запусков."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.connectors.base import DocumentRecord
from app.db.models import IngestionRun, IngestionSource, SourceDocument
from app.db.session import Database
from app.services.ingestion import ingestion_lock, store_page


class Batch:
    """Результат одного чтения источника; complete — получен полный снимок."""

    def __init__(
        self,
        records: list[DocumentRecord],
        errors: list[dict[str, str]],
        has_more: bool = False,
        skipped: int = 0,
        complete: bool = False,
    ):
        self.records, self.errors = records, errors
        self.has_more, self.skipped, self.complete = has_more, skipped, complete


async def sync_personal_source(
    database: Database,
    *,
    source_id: str,
    kind: str,
    name: str,
    base_url: str,
    scope: dict[str, Any],
    collect: Callable[[], Awaitable[Batch]],
) -> dict:
    async with ingestion_lock(database, f"hse:{source_id}:sync"):
        run_id = uuid4()
        async with database.session() as session, session.begin():
            await session.execute(
                insert(IngestionSource)
                .values(id=source_id, kind=kind, name=name, base_url=base_url, config=scope)
                .on_conflict_do_update(
                    index_elements=["id"], set_={"base_url": base_url, "config": scope}
                )
            )
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.source_id == source_id, IngestionRun.status == "running")
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
            session.add(IngestionRun(id=run_id, source_id=source_id, status="running", scope=scope))

        counts = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
        try:
            batch = await collect()
            for record in batch.records:
                counts[await store_page(database, record)] += 1
            if batch.complete and not batch.errors and not batch.has_more:
                # Полный снимок: исчезнувшая запись (например, сданное задание) больше
                # не актуальна. История версий при этом сохраняется.
                seen = {record.external_id for record in batch.records}
                conditions = [
                    SourceDocument.source_id == source_id,
                    SourceDocument.is_active.is_(True),
                ]
                if seen:
                    conditions.append(SourceDocument.external_id.not_in(seen))
                async with database.session() as session, session.begin():
                    result = await session.execute(
                        update(SourceDocument).where(*conditions).values(is_active=False)
                    )
                    counts["deactivated"] = result.rowcount or 0
        except BaseException:
            async with database.session() as session, session.begin():
                run = await session.get(IngestionRun, run_id)
                run.status = "interrupted"
                run.finished_at = datetime.now(UTC)
            raise

        errors = batch.errors
        # Пустой ответ без ошибок — успешная синхронизация, а не сбой.
        status = "success" if not errors else "partial" if batch.records else "failed"
        finished_at = datetime.now(UTC)
        async with database.session() as session, session.begin():
            run = await session.get(IngestionRun, run_id)
            run.status, run.finished_at = status, finished_at
            run.created_count = counts["created"]
            run.updated_count = counts["updated"]
            run.unchanged_count = counts["unchanged"]
            run.errors = errors[:50]
            run.scope = {**scope, "has_more": batch.has_more, "skipped": batch.skipped}
            source = await session.get(IngestionSource, source_id)
            source.last_error = errors[0]["code"] if errors else None
            if status == "success":
                source.last_synced_at = finished_at
        return {
            "run_id": str(run_id),
            "status": status,
            **counts,
            "skipped": batch.skipped,
            "errors": errors,
            "has_more": batch.has_more,
        }
