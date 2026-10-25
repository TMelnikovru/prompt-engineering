#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
deploy_root="${1:-$HOME/apps/hse-study-agent-postgresql}"
if [[ -f "$deploy_root/deploy.env" ]]; then
    source "$deploy_root/deploy.env"
fi
deploy_project="${HSE_DEPLOY_PROJECT:-hse-study-agent-postgresql}"
exec 9>"$deploy_root/wiki-sync.lock"
flock -n 9 || exit 0
cd "$deploy_root/current"
export HSE_APP_IMAGE="$deploy_project:$(basename "$(readlink -f "$deploy_root/current")")"
docker compose --project-name "$deploy_project" --env-file "$deploy_root/.env" \
    exec -T app python -m app.ingest sync-wiki --analyze --limit 5
