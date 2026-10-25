#!/usr/bin/env bash
# Prepare private server files. Register the printed public key as a read-only
# GitHub deploy key before cloning the repository (see docs/DEPLOYMENT.md).
set -Eeuo pipefail
umask 077

deploy_root="${1:-$HOME/apps/hse-study-agent}"
mkdir -p "$deploy_root/releases"
chmod 700 "$deploy_root"

if [[ ! -f "$deploy_root/.env" ]]; then
    python3 - "$deploy_root/.env" <<'PY'
import secrets
import sys
from pathlib import Path

password = secrets.token_hex(32)
Path(sys.argv[1]).write_text(
    "HSE_BIND_HOST=127.0.0.1\n"
    "HSE_PORT=8000\n"
    "HSE_DEMO_MODE=false\n"
    "HSE_TIMEZONE=Europe/Moscow\n"
    "POSTGRES_DB=hse_study_agent\n"
    "POSTGRES_USER=hse_agent\n"
    f"POSTGRES_PASSWORD={password}\n"
    f"HSE_DATABASE_URL=postgresql+psycopg://hse_agent:{password}@postgres:5432/hse_study_agent\n"
    "OPENAI_API_KEY=\n"
    "OPENAI_MODEL=\n"
)
PY
fi
chmod 600 "$deploy_root/.env"

if [[ ! -f "$deploy_root/github_ed25519" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C hse-study-agent-docker-prod \
        -f "$deploy_root/github_ed25519"
fi
cat "$deploy_root/github_ed25519.pub"
