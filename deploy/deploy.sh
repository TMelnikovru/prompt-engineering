#!/usr/bin/env bash
# Installed outside the checkout so failed releases cannot replace the deployer.
set -Eeuo pipefail
umask 077

deploy_root="${1:-$HOME/apps/hse-study-agent}"
revision="${2:-main}"
export GIT_TERMINAL_PROMPT=0

exec 9>"$deploy_root/deploy.lock"
flock -n 9 || exit 0

if [[ "$revision" == main ]]; then
    git -C "$deploy_root/repository" fetch --quiet origin \
        +refs/heads/main:refs/remotes/origin/main
    revision="$(git -C "$deploy_root/repository" rev-parse refs/remotes/origin/main)"
elif [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Use main or a full commit SHA.' >&2
    exit 2
fi

previous=""
if [[ -L "$deploy_root/current" ]]; then
    previous="$(basename "$(readlink "$deploy_root/current")")"
fi
if [[ "$revision" == "$previous" && "${HSE_FORCE_DEPLOY:-0}" != 1 ]]; then
    exit 0
fi

release="$deploy_root/releases/$revision"
mkdir -p "$release"
# Source files must remain readable by the image's non-root application user.
# The outer deployment directory stays private (0700).
git -C "$deploy_root/repository" archive "$revision" | (umask 022; tar -x -C "$release")

compose_for() {
    local commit="$1"
    shift
    HSE_APP_IMAGE="hse-study-agent:$commit" docker compose \
        --project-name hse-study-agent \
        --env-file "$deploy_root/.env" \
        --project-directory "$deploy_root/releases/$commit" \
        -f "$deploy_root/releases/$commit/compose.yaml" "$@"
}

echo "$(date -Is) Building $revision"
compose_for "$revision" config --quiet
# A failed build leaves the running release untouched.
compose_for "$revision" build app

rollback() {
    local result="$?"
    trap - ERR
    echo "$(date -Is) Deployment failed for $revision" >&2
    if [[ -n "$previous" ]]; then
        echo "$(date -Is) Restoring $previous" >&2
        compose_for "$previous" up -d --no-build --wait --wait-timeout 120 || \
            echo 'Rollback failed; inspect the containers.' >&2
    fi
    exit "$result"
}
trap rollback ERR

compose_for "$revision" up -d --no-build --wait --wait-timeout 120
# Verify authentication and a real SQL roundtrip from the application container.
compose_for "$revision" exec -T app python -c \
    'import psycopg; c = psycopg.connect(); assert c.execute("SELECT 1").fetchone() == (1,); c.close()'
compose_for "$revision" exec -T app python -c \
    'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8000/", timeout=5).status == 200'

ln -sfn "$release" "$deploy_root/current.next"
mv -Tf "$deploy_root/current.next" "$deploy_root/current"
if [[ -n "$previous" && "$previous" != "$revision" ]]; then
    printf '%s\n' "$previous" > "$deploy_root/previous"
fi
trap - ERR
echo "$(date -Is) Deployed $revision"
