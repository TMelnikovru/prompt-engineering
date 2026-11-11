"""DSBA CLI and server worker; both use the same logic as the frontend endpoint."""

import argparse
import asyncio
import json

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.connectors.dsba import DsbaConnector, DsbaError, DsbaSelection
from app.db.session import Database
from app.services.dsba import refresh_dsba_profiles, sync_dsba
from app.services.ingestion import SyncBusy


async def execute(args) -> int:
    settings = Settings()
    if args.command == "catalog":
        async with DsbaConnector(settings) as connector:
            print(json.dumps(await connector.catalog(), ensure_ascii=False))
        return 0
    if settings.database_url is None:
        print(json.dumps({"error": "database_not_configured"}), flush=True)
        return 2
    database = Database(settings)
    try:
        if args.command == "sync":
            selection = DsbaSelection(
                program=args.program,
                year=args.year,
                group=args.group,
                sub=args.sub,
                subjects=args.subject or [],
            )
            result = await sync_dsba(database, settings, selection, force=True)
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
            return 2 if result["status"] == "failed" else 0
        while True:
            try:
                print(
                    json.dumps(
                        {
                            "event": "dsba_refresh",
                            "results": await refresh_dsba_profiles(database, settings),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except SyncBusy:
                pass
            except (SQLAlchemyError, DsbaError):
                print(
                    json.dumps({"event": "dsba_refresh", "error": "refresh_unavailable"}),
                    flush=True,
                )
            await asyncio.sleep(30)
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")
    commands.add_parser("worker")
    sync = commands.add_parser("sync")
    sync.add_argument("--program", required=True)
    sync.add_argument("--year", required=True, type=int)
    sync.add_argument("--group", default="")
    sync.add_argument("--sub", default="")
    sync.add_argument("--subject", action="append")
    try:
        raise SystemExit(asyncio.run(execute(parser.parse_args())))
    except (DsbaError, SyncBusy, SQLAlchemyError) as exc:
        code = exc.code if isinstance(exc, DsbaError) else "operation_unavailable"
        print(json.dumps({"error": code}), flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
