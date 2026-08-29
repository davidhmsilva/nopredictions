#!/usr/bin/env bash
# NOPREDICTIONS — In-play CONVERGENCE daemon (SHADOW phase)
# Runs forever with goal detection:
#   - Polls api-football every 60s for live scores (~660 req/day during 11h match window)
#   - On goal: immediately runs a full scan cycle
#   - Full scan every 300s regardless
# With api-football PRO (7500 req/day) this runs comfortably all day.

set -euo pipefail

LOCKFILE="/tmp/nopredictions_convergence.lock"
LOGFILE="/Users/davidsilva/agente/agent/convergence_daemon.log"
PYTHON="/Users/davidsilva/agente/ingest/.venv/bin/python"
WORKDIR="/Users/davidsilva/agente"

if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') — convergence daemon already running (pid $(cat "$LOCKFILE")), skipping" \
        >> "$LOGFILE"
    exit 0
fi

cd "$WORKDIR"

echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

echo "$(date -u '+%Y-%m-%d %H:%M UTC') — convergence daemon starting (forever mode)" >> "$LOGFILE"

"$PYTHON" agent/convergence_trader.py \
    --forever \
    >> "$LOGFILE" 2>&1
