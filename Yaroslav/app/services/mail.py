"""Синхронизация почты в общую схему источников, документов и версий."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.connectors.mail import MailBatch, MailConnector, MailError
from app.db.models import IngestionRun, IngestionSource
from app.db.session import Database
from app.services.ingestion import ingestion_lock, store_page

SOURCE_ID = "mail"


async def sync_mail(database: Database, settings: Settings, batch: MailBatch | None = None) -> dict:
    """Одна логика для воркера и ручного запуска.

    Письма за пределами окна `mail_lookback_days` не снимаются с публикации:
    окно сдвигается, а старое письмо от этого не перестаёт существовать.
    """
    async with ingestion_lock(database, "hse:mail:sync"):
        scope = {
            "host": settings.mail_imap_host,
            "folders": settings.mail_folders,
            "lookback_days": settings.mail_lookback_days,
            "max_messages": settings.mail_max_messages,
            "allowed_sender_domains": settings.mail_allowed_sender_domains,
            "interval_seconds": settings.mail_interval_seconds,
            "transport": "collector" if batch else "direct",
        }
        run_id = uuid4()
        async with database.session() as session, session.begin():
            await session.execute(
                insert(IngestionSource)
                .values(
                    id=SOURCE_ID,
                    kind="mail",
                    name="Почта",
                    base_url=settings.mail_web_url,
                    config=scope,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"base_url": settings.mail_web_url, "config": scope},
                )
            )
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.source_id == SOURCE_ID, IngestionRun.status == "running")
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
            session.add(IngestionRun(id=run_id, source_id=SOURCE_ID, status="running", scope=scope))

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        errors: list[dict[str, str]] = []
        try:
            if batch is None:
                try:
                    batch = await MailConnector(settings).collect()
                except MailError as exc:
                    batch = MailBatch(errors=[{"code": exc.code}])
            errors.extend(batch.errors)
            for message in batch.messages:
                counts[await store_page(database, message)] += 1
        except BaseException:
            async with database.session() as session, session.begin():
                run = await session.get(IngestionRun, run_id)
                run.status = "interrupted"
                run.finished_at = datetime.now(UTC)
            raise

        # Пустой ящик без ошибок — успешная синхронизация, а не сбой.
        if not errors:
            status = "success"
        elif batch.messages:
            status = "partial"
        else:
            status = "failed"
        finished_at = datetime.now(UTC)
        async with database.session() as session, session.begin():
            run = await session.get(IngestionRun, run_id)
            run.status, run.finished_at = status, finished_at
            run.created_count = counts["created"]
            run.updated_count = counts["updated"]
            run.unchanged_count = counts["unchanged"]
            run.errors = errors[:50]
            run.scope = {**scope, "has_more": batch.has_more, "skipped": batch.skipped}
            source = await session.get(IngestionSource, SOURCE_ID)
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
