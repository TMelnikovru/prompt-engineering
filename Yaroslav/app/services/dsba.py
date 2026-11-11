from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.api.schemas import Evidence, Material, Task
from app.config import Settings
from app.connectors.dsba import DsbaConnector, DsbaError, DsbaSelection
from app.db.models import DocumentVersion, IngestionRun, IngestionSource, SourceDocument
from app.db.session import Database
from app.services.ingestion import ingestion_lock, store_page


def run_result(run: IngestionRun, source_id: str, cached: bool = False) -> dict:
    return {
        "source_id": source_id,
        "run_id": str(run.id),
        "status": run.status,
        "created": run.created_count,
        "updated": run.updated_count,
        "unchanged": run.unchanged_count,
        "errors": run.errors,
        "has_more": run.scope.get("has_more", False),
        "scope": run.scope,
        "cached": cached,
    }


async def sync_dsba(
    database: Database, settings: Settings, selection: DsbaSelection, *, force: bool = False
) -> dict:
    source_id = selection.source_id
    # A global DSBA lock bounds upstream concurrency across browser-selected profiles.
    async with ingestion_lock(database, "hse:dsba:sync"):
        async with database.session() as session:
            source = await session.get(IngestionSource, source_id)
            previous = await session.scalar(
                select(IngestionRun)
                .where(IngestionRun.source_id == source_id)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            if (
                not force
                and source is not None
                and source.base_url == settings.dsba_base_url
                and previous
                and previous.finished_at
                and (previous.finished_at > datetime.now(UTC) - timedelta(seconds=60))
            ):
                return run_result(previous, source_id, cached=True)
            if source is None:
                count = await session.scalar(
                    select(func.count())
                    .select_from(IngestionSource)
                    .where(IngestionSource.kind == "dsba")
                )
                if count >= settings.dsba_max_profiles:
                    raise DsbaError("dsba_profile_limit")
            existing_scope = source.config if source else None

        scope = {
            "selection": selection.model_dump(),
            "interval_seconds": settings.dsba_interval_seconds,
            "transport": "direct",
            "has_more": False,
        }
        catalog_error = None
        async with DsbaConnector(settings) as connector:
            try:
                catalog = await connector.catalog()
                connector.validate(selection, catalog)
                scope["academic_year"] = catalog["academic_year"]
            except DsbaError as exc:
                if existing_scope is None or (exc.code == "dsba_invalid_selection" and not force):
                    raise
                catalog_error = exc
                scope = {**existing_scope, **scope}

            async with database.session() as session, session.begin():
                source = await session.get(IngestionSource, source_id)
                if source is None:
                    source = IngestionSource(
                        id=source_id,
                        kind="dsba",
                        name=f"DSBA Online · {selection.program.upper()} · {selection.year} курс"
                        + (f" · {selection.group}" if selection.group else ""),
                        base_url=settings.dsba_base_url,
                        config=scope,
                    )
                    session.add(source)
                else:
                    source.config = scope
                    source.base_url = settings.dsba_base_url
                await session.execute(
                    update(IngestionRun)
                    .where(IngestionRun.source_id == source_id, IngestionRun.status == "running")
                    .values(status="interrupted", finished_at=datetime.now(UTC))
                )
                run = IngestionRun(source_id=source_id, status="running", scope=scope)
                session.add(run)
                await session.flush()
                run_id = run.id

            try:
                if catalog_error:
                    errors = [{"title": "catalog", "code": catalog_error.code}]
                    records, complete, has_more = [], set(), False
                else:
                    batch = await connector.collect(selection)
                    errors, records = batch.errors, batch.records
                    complete, has_more = batch.complete_kinds, batch.has_more
                counts = {"created": 0, "updated": 0, "unchanged": 0}
                for record in records:
                    counts[await store_page(database, record)] += 1
                # Only complete endpoint snapshots can retire missing records.
                # A timeout, malformed row or configured limit keeps prior data.
                async with database.session() as session, session.begin():
                    for kind in complete:
                        current_ids = [
                            r.external_id for r in records if r.attributes["kind"] == kind
                        ]
                        await session.execute(
                            update(SourceDocument)
                            .where(
                                SourceDocument.source_id == source_id,
                                SourceDocument.external_id.startswith(kind + ":"),
                                SourceDocument.external_id.not_in(current_ids),
                            )
                            .values(is_active=False)
                        )
                    run = await session.get(IngestionRun, run_id)
                    run.status = (
                        "partial" if errors and records else "failed" if errors else "success"
                    )
                    run.finished_at = datetime.now(UTC)
                    run.created_count = counts["created"]
                    run.updated_count = counts["updated"]
                    run.unchanged_count = counts["unchanged"]
                    run.errors = errors[:50]
                    run.scope = {**scope, "has_more": has_more}
                    source = await session.get(IngestionSource, source_id)
                    source.last_error = errors[0]["code"] if errors else None
                    source.config = run.scope
                    if run.status == "success":
                        source.last_synced_at = run.finished_at
                    return run_result(run, source_id)
            except BaseException:
                async with database.session() as session, session.begin():
                    run = await session.get(IngestionRun, run_id)
                    run.status = "interrupted"
                    run.finished_at = datetime.now(UTC)
                raise


async def dsba_tasks(database: Database, source_id: str) -> list[Task]:
    async with database.session() as session:
        rows = (
            await session.execute(
                select(SourceDocument, DocumentVersion)
                .join(DocumentVersion, SourceDocument.current_version_id == DocumentVersion.id)
                .join(IngestionSource, SourceDocument.source_id == IngestionSource.id)
                .where(
                    IngestionSource.id == source_id,
                    IngestionSource.kind == "dsba",
                    SourceDocument.is_active.is_(True),
                    DocumentVersion.attributes["kind"].astext == "deadline",
                )
            )
        ).all()
        tasks = []
        for doc, version in rows:
            facts = version.attributes["facts"]
            evidence = [
                Evidence(
                    source_id=source_id,
                    title=doc.title,
                    quote=version.content_text,
                    url=doc.url,
                    fetched_at=version.fetched_at,
                    external_id=doc.external_id,
                )
            ]
            change = version.attributes.get("deadline_change")
            if change:
                evidence.append(
                    Evidence(
                        source_id=source_id,
                        title="Предыдущая версия срока",
                        quote=change["previous_evidence"],
                        url=doc.url,
                        fetched_at=change.get("previous_fetched_at"),
                    )
                )
            tasks.append(
                Task(
                    id=str(doc.id),
                    title=facts["title"],
                    course=facts["course"],
                    priority="medium",
                    due_at=facts.get("due_at"),
                    due_date=facts.get("due_date"),
                    materials=[item["url"] for item in version.attributes.get("links", [])],
                    evidence=evidence,
                    due_requires_confirmation=bool(facts.get("conflict")),
                    deadline_changed=(
                        f"Источник изменил срок: {change['from'] or 'не указан'} "
                        f"→ {change['to'] or 'не указан'}"
                        if change
                        else None
                    ),
                )
            )
        return sorted(
            tasks,
            key=lambda t: (
                t.due_at.date().isoformat()
                if t.due_at
                else t.due_date.isoformat()
                if t.due_date
                else "9999",
                t.due_at.isoformat() if t.due_at else "",
                t.id,
            ),
        )


async def dsba_materials(database: Database, source_id: str) -> list[Material]:
    """Read the selected group's active library cards, without a new upstream call."""
    async with database.session() as session:
        rows = (
            await session.execute(
                select(SourceDocument, DocumentVersion)
                .join(DocumentVersion, SourceDocument.current_version_id == DocumentVersion.id)
                .where(
                    SourceDocument.source_id == source_id,
                    SourceDocument.is_active.is_(True),
                    SourceDocument.external_id.startswith("material:"),
                )
                .order_by(SourceDocument.title, SourceDocument.id)
                .limit(1000)
            )
        ).all()
        return [
            Material(
                id=str(document.id),
                title=document.title.removeprefix(version.attributes.get("course", "") + " · "),
                course=version.attributes.get("course") or "Предмет не указан",
                source_id=document.source_id,
                url=document.url,
                fetched_at=version.fetched_at,
            )
            for document, version in rows
        ]


async def refresh_dsba_profiles(database: Database, settings: Settings) -> list[dict]:
    async with database.session() as session:
        profiles = (
            await session.scalars(
                select(IngestionSource)
                .where(IngestionSource.kind == "dsba")
                .order_by(IngestionSource.created_at)
                .limit(settings.dsba_max_profiles)
            )
        ).all()
        due = []
        for profile in profiles:
            last = await session.scalar(
                select(IngestionRun)
                .where(IngestionRun.source_id == profile.id)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            delay = 60 if profile.last_error else settings.dsba_interval_seconds
            if last is None or last.started_at < datetime.now(UTC) - timedelta(seconds=delay):
                due.append(profile.config["selection"])
    results = []
    for value in due:
        try:
            result = await sync_dsba(
                database, settings, DsbaSelection.model_validate(value), force=True
            )
            results.append({"source_id": result["source_id"], "status": result["status"]})
        except DsbaError as exc:
            results.append(
                {"source_id": DsbaSelection.model_validate(value).source_id, "error": exc.code}
            )
    return results
