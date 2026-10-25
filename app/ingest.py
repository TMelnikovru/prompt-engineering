"""CLI чтения источников и двух этапов ИИ-обработки; секреты только из окружения."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.wiki import WikiBatch
from app.db.models import AiArtifact, DocumentVersion, SourceDocument
from app.db.session import Database
from app.services.analysis import (
    PROMPT_VERSION,
    AiError,
    analyze_document,
    extraction_fingerprint,
    summarize,
)
from app.services.ingestion import SyncBusy, sync_wiki


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def analyze_pending(database: Database, settings: Settings, limit: int) -> dict:
    async with database.session() as session:
        rows = (
            await session.execute(
                select(SourceDocument.id, DocumentVersion.id, DocumentVersion.content_hash)
                .join(DocumentVersion, SourceDocument.current_version_id == DocumentVersion.id)
                .order_by(SourceDocument.title, SourceDocument.id)
            )
        ).all()
        artifacts = (
            await session.scalars(
                select(AiArtifact).where(
                    AiArtifact.kind == "extraction",
                    AiArtifact.model == settings.extraction_model,
                    AiArtifact.prompt_version == PROMPT_VERSION,
                )
            )
        ).all()
        covered = {artifact.input_fingerprint: set(artifact.inputs) for artifact in artifacts}
        pending = [
            doc_id
            for doc_id, version_id, content_hash in rows
            if str(version_id)
            not in covered.get(extraction_fingerprint(doc_id, content_hash, settings), set())
        ]
    results = []
    for doc_id in pending[:limit]:
        try:
            result = await analyze_document(database, settings, doc_id)
            results.append(
                {k: result[k] for k in ("id", "cached", "model", "input_tokens", "output_tokens")}
            )
        except (AiError, SyncBusy) as exc:
            results.append(
                {
                    "document_id": str(doc_id),
                    "error": str(exc) if isinstance(exc, AiError) else "analysis_busy",
                }
            )
    completed = sum("error" not in result for result in results)
    return {
        "pending_before": len(pending),
        "remaining": len(pending) - completed,
        "results": results,
    }


def stage_error(exc: Exception) -> dict:
    if isinstance(exc, SyncBusy):
        return {"error": "operation_in_progress"}
    if isinstance(exc, AiError):
        return {"error": str(exc)}
    # Database exception messages can include connection credentials.
    return {"error": "database_unavailable"}


async def worker_cycle(database: Database, settings: Settings, args) -> dict:
    """Each stage can recover independently; pending AI work lives in PostgreSQL."""
    result = {"event": "wiki_cycle", "started_at": datetime.now(UTC).isoformat()}
    try:
        result["sync"] = await sync_wiki(database, settings)
    except (SyncBusy, SQLAlchemyError) as exc:
        result["sync"] = stage_error(exc)
    if args.analyze:
        try:
            result["analysis"] = await analyze_pending(database, settings, args.limit)
        except (AiError, SyncBusy, SQLAlchemyError) as exc:
            result["analysis"] = stage_error(exc)
    if args.summarize:
        try:
            summary = await summarize(database, settings, args.summary_limit)
            result["summary"] = {key: value for key, value in summary.items() if key != "payload"}
        except (AiError, SyncBusy, SQLAlchemyError) as exc:
            if isinstance(exc, AiError) and str(exc) == "no_analyzed_documents":
                result["summary"] = {"skipped": "no_analyzed_documents"}
            else:
                result["summary"] = stage_error(exc)
    result["retry_required"] = (
        result["sync"].get("status") != "success"
        or any(
            "error" in stage for stage in (result.get("analysis", {}), result.get("summary", {}))
        )
        or result.get("analysis", {}).get("remaining", 0) > 0
    )
    return result


def retry_delay(settings: Settings, failures: int) -> int:
    if not failures:
        return settings.wiki_interval_seconds
    return min(
        settings.wiki_interval_seconds, settings.wiki_retry_seconds * 2 ** min(failures - 1, 10)
    )


async def run_worker(database: Database, settings: Settings, args):
    failures = 0
    while True:
        result = await worker_cycle(database, settings, args)
        failures = failures + 1 if result["retry_required"] else 0
        delay = retry_delay(settings, failures)
        emit({**result, "next_attempt_in_seconds": delay})
        await asyncio.sleep(delay)


async def execute(args) -> int:
    settings = Settings()
    updates = {}
    if getattr(args, "max_pages", None) is not None:
        updates["wiki_max_pages"] = args.max_pages
    if getattr(args, "depth", None) is not None:
        updates["wiki_max_depth"] = args.depth
    if getattr(args, "seed", None):
        updates["wiki_seeds"] = args.seed
    settings = Settings(**{**settings.model_dump(), **updates})
    if settings.database_url is None:
        emit({"error": "database_not_configured"})
        return 2
    database = Database(settings)
    try:
        if args.command == "worker":
            await run_worker(database, settings, args)
        elif args.command == "sync-wiki":
            result = await worker_cycle(database, settings, args)
            emit(result)
            return 2 if result["retry_required"] else 0
        elif args.command == "import-wiki":
            batch = WikiBatch.model_validate_json(sys.stdin.read(100_000_000))
            emit(await sync_wiki(database, settings, batch=batch))
        elif args.command == "analyze":
            if args.document:
                result = await analyze_document(database, settings, UUID(args.document))
                emit({k: v for k, v in result.items() if k != "payload"})
            else:
                emit(await analyze_pending(database, settings, args.limit))
        elif args.command == "summarize":
            result = await summarize(database, settings, args.limit)
            emit({k: v for k, v in result.items() if k != "payload"})
        return 0
    except SyncBusy:
        emit({"error": "sync_in_progress"})
        return 3
    except AiError as exc:
        emit({"error": str(exc)})
        return 2
    except SQLAlchemyError:
        emit({"error": "database_unavailable"})
        return 2
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import-wiki")
    for name in ("sync-wiki", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--max-pages", type=int)
        command.add_argument("--depth", type=int)
        command.add_argument("--seed", action="append")
        command.add_argument("--analyze", action="store_true")
        command.add_argument("--summarize", action="store_true")
        command.add_argument("--limit", type=int, choices=range(1, 51), default=5)
        command.add_argument("--summary-limit", type=int, choices=range(1, 51), default=20)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--document")
    analyze.add_argument("--limit", type=int, choices=range(1, 51), default=5)
    summary = commands.add_parser("summarize")
    summary.add_argument("--limit", type=int, choices=range(1, 51), default=20)
    try:
        raise SystemExit(asyncio.run(execute(parser.parse_args())))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
