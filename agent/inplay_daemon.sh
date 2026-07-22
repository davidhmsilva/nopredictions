#!/usr/bin/env bash
# NOPREDICTIONS — In-play Poisson daemon v4 (continuous)
# Polls Polymarket in-play markets every 10 minutes, 24/7.
# Hourly cron retries if the process is dead; lockfile prevents overlap.
# Features: DC model lambdas, aggressive trailing push, spread tracking,
# live odds consensus (Pinnacle/Betfair), line movement detection, Filter G live gate.

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

# Continuous mode: 10-min polling, effectively forever (99999 cycles × 600s ≈ 70 days).
# Hourly cron + lockfile = restart within ≤1h if process crashes.
"$PYTHON" agent/run.py \
    --strategy poisson \
    --cycles 99999 \
    --interval 600 \
    >> "$LOGFILE" 2>&1
