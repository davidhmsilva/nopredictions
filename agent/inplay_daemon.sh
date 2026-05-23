#!/usr/bin/env bash
# NOPREDICTIONS — In-play Poisson daemon v3
# Polls Polymarket in-play markets every 10 minutes for ~2 hours.
# Features: DC model lambdas, aggressive trailing push, spread tracking,
# live odds consensus (Pinnacle/Betfair), line movement detection.
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

# 10-min polling: 12 cycles = 120 min (2 hours)
# With live odds consensus + line movement detection
"$PYTHON" agent/run.py \
    --strategy poisson \
    --cycles 12 \
    --interval 600 \
    >> "$LOGFILE" 2>&1
