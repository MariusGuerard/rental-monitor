#!/bin/bash
# Wrapper invoked by launchd each interval. Keeps the venv + cwd correct and
# appends a heartbeat so we can tell the scheduler is alive.
cd "$(dirname "$0")" || exit 1
# Load secrets (Telegram token, chat IDs, packet URL) if present.
[ -f .env ] && set -a && . ./.env && set +a
exec ./.venv/bin/python main.py "$@"
