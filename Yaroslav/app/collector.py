"""Сбор публичной вики из доступной сети и передача в серверную БД через SSH."""

import argparse
import asyncio
import json
import shlex
import subprocess

from app.config import Settings
from app.connectors.wiki import collect_wiki


async def run(args):
    settings = Settings(wiki_max_pages=args.max_pages)
    root = shlex.quote(args.root)
    command = (
        "set -e; deploy_root=" + root + '; cd "$deploy_root/current"; '
        'export HSE_APP_IMAGE="hse-study-agent-postgresql:$(basename '
        '"$(readlink -f "$deploy_root/current")")"; '
        "docker compose --project-name hse-study-agent-postgresql "
        '--env-file "$deploy_root/.env" exec -T app python -m app.ingest import-wiki'
    )
    if args.analyze:
        command += (
            "; docker compose --project-name hse-study-agent-postgresql "
            '--env-file "$deploy_root/.env" exec -T app python -m app.ingest analyze --limit '
            + str(args.analyze_limit)
        )
    while True:
        try:
            batch = await collect_wiki(settings)
            if not batch.pages:
                print(
                    json.dumps(
                        {"error": "collector_source_unavailable", "details": batch.errors},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.target, command],
                    input=batch.model_dump_json(),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if result.returncode:
                    print(
                        json.dumps(
                            {"error": "collector_delivery_failed", "exit_code": result.returncode}
                        ),
                        flush=True,
                    )
                else:
                    print(result.stdout.strip(), flush=True)
        except (OSError, subprocess.TimeoutExpired):
            print(json.dumps({"error": "collector_delivery_unavailable"}), flush=True)
        if args.once:
            return
        await asyncio.sleep(settings.wiki_interval_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="docker-prod")
    parser.add_argument("--root", default="/home/tmelnikovru/apps/hse-study-agent-postgresql")
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--analyze-limit", type=int, choices=range(1, 51), default=5)
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
