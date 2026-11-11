#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

deploy_root="${1:-$HOME/apps/hse-study-agent}"
if [[ -f "$deploy_root/deploy.env" ]]; then
    source "$deploy_root/deploy.env"
fi
deploy_branch="${HSE_DEPLOY_BRANCH:-main}"
deploy_project="${HSE_DEPLOY_PROJECT:-hse-study-agent}"
if [[ ! "$deploy_project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo 'Use a lowercase Docker Compose project name.' >&2
    exit 2
fi
if [[ ! "$deploy_root" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
    echo 'Deployment path must be absolute and contain no spaces or shell metacharacters.' >&2
    exit 2
fi
test -f "$deploy_root/deploy.sh"
test -f "$deploy_root/.env"
test -d "$deploy_root/repository/.git"

cron_tmp="$(mktemp)"
trap 'rm -f "$cron_tmp"' EXIT
{ crontab -l 2>/dev/null || true; } | \
    sed "/# $deploy_project-autodeploy\$/d" > "$cron_tmp"
printf '* * * * * PATH=/usr/local/bin:/usr/bin:/bin /bin/bash %s/deploy.sh %s >> %s/deploy.log 2>&1 # %s-autodeploy\n' \
    "$deploy_root" "$deploy_root" "$deploy_root" "$deploy_project" >> "$cron_tmp"
crontab "$cron_tmp"
printf 'Automatic deployment of %s enabled for %s (every minute).\n' "$deploy_branch" "$deploy_project"
