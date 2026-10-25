#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

deploy_root="${1:-$HOME/apps/hse-study-agent}"
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
    sed '/# hse-study-agent-autodeploy$/d' > "$cron_tmp"
printf '* * * * * PATH=/usr/local/bin:/usr/bin:/bin /bin/bash %s/deploy.sh %s >> %s/deploy.log 2>&1 # hse-study-agent-autodeploy\n' \
    "$deploy_root" "$deploy_root" "$deploy_root" >> "$cron_tmp"
crontab "$cron_tmp"
echo 'Automatic deployment of main enabled (every minute).'
