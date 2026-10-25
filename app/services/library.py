from uuid import UUID

from sqlalchemy import func, select

from app.db.models import AiArtifact, DocumentVersion, IngestionRun, IngestionSource, SourceDocument
from app.db.session import Database


async def list_sources(database: Database) -> list[dict]:
    async with database.session() as session:
        sources = (await session.scalars(select(IngestionSource))).all()
        result = []
        for source in sources:
            count = await session.scalar(
                select(func.count())
                .select_from(SourceDocument)
                .where(SourceDocument.source_id == source.id)
            )
            run = await session.scalar(
                select(IngestionRun)
                .where(IngestionRun.source_id == source.id)
                .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
                .limit(1)
            )
            status = (
                "syncing"
                if run and run.status == "running"
                else "stale"
                if source.last_error and source.last_synced_at
                else "error"
                if source.last_error
                else "connected"
                if source.last_synced_at
                else "not_connected"
            )
            result.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "kind": source.kind,
                    "base_url": source.base_url,
                    "status": status,
                    "document_count": count,
                    "last_synced_at": source.last_synced_at,
                    "last_error": source.last_error,
                    "scope": source.config,
                    "last_run": {
                        "id": str(run.id),
                        "status": run.status,
                        "created": run.created_count,
                        "updated": run.updated_count,
                        "unchanged": run.unchanged_count,
                        "errors": run.errors,
                        "scope": run.scope,
                    }
                    if run
                    else None,
                }
            )
        return result


async def list_documents(database: Database, source_id: str, limit: int, offset: int) -> dict:
    async with database.session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.source_id == source_id)
        )
        rows = (
            await session.execute(
                select(SourceDocument, DocumentVersion)
                .join(DocumentVersion, SourceDocument.current_version_id == DocumentVersion.id)
                .where(SourceDocument.source_id == source_id)
                .order_by(SourceDocument.last_seen_at.desc(), SourceDocument.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": str(doc.id),
                    "source_id": doc.source_id,
                    "external_id": doc.external_id,
                    "title": doc.title,
                    "url": doc.url,
                    "version_id": str(version.id),
                    "content_hash": version.content_hash,
                    "excerpt": version.content_text[:350],
                    "last_seen_at": doc.last_seen_at,
                }
                for doc, version in rows
            ],
        }


async def get_document(database: Database, document_id: UUID) -> dict | None:
    async with database.session() as session:
        row = (
            await session.execute(
                select(SourceDocument, DocumentVersion)
                .join(DocumentVersion, SourceDocument.current_version_id == DocumentVersion.id)
                .where(SourceDocument.id == document_id)
            )
        ).first()
        if not row:
            return None
        doc, version = row
        version_count = await session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
        )
        analysis = await session.scalar(
            select(AiArtifact)
            .where(AiArtifact.kind == "extraction", AiArtifact.inputs.contains([str(version.id)]))
            .order_by(AiArtifact.created_at.desc())
            .limit(1)
        )
        return {
            "id": str(doc.id),
            "source_id": doc.source_id,
            "title": doc.title,
            "url": doc.url,
            "version_id": str(version.id),
            "content_hash": version.content_hash,
            "content_text": version.content_text,
            "attributes": version.attributes,
            "fetched_at": version.fetched_at,
            "last_seen_at": doc.last_seen_at,
            "version_count": version_count,
            "analysis": {
                "id": str(analysis.id),
                "model": analysis.model,
                "payload": analysis.payload,
            }
            if analysis
            else None,
        }
