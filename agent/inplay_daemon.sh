#!/usr/bin/env bash
# NOPREDICTIONS — In-play Poisson daemon v2
# Polls Polymarket in-play markets every 5 minutes for ~2 hours.
# Uses DC model lambdas + live pressure tracker from api-football.
# Intended to be called by cron during match windows.

set -euo pipefail

LOCKFILE="/tmp/nopredictions_inplay.lock"
LOGFILE="/Users/davidsilva/Documents/agente/agent/inplay_daemon.log"
PYTHON="/Users/davidsilva/Documents/agente/ingest/.venv/bin/python"
WORKDIR="/Users/davidsilva/Documents/agente"

if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') — daemon already running (pid $(cat "$LOCKFILE")), skipping" \
        >> "$LOGFILE"
    exit 0
fi

cd "$WORKDIR"

echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

"$PYTHON" agent/run.py \
    --strategy poisson \
    --cycles 24 \
    --interval 300 \
    >> "$LOGFILE" 2>&1
