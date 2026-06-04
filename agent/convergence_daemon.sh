#!/usr/bin/env bash
# NOPREDICTIONS — In-play CONVERGENCE daemon (SHADOW phase)
# Polls live matches every 5 minutes for ~2.5 hours, logging shadow entries +
# exits to the convergence_shadow ledger. No real money. Intended to be called
# by cron during match windows, alongside (or instead of) inplay_daemon.sh.
#
# NOTE: api-football free tier is 100 req/day. This polls 1x/cycle (~30/session).
# Running this AND inplay_daemon.sh in the same window may exceed the budget.

set -euo pipefail

LOCKFILE="/tmp/nopredictions_convergence.lock"
LOGFILE="/Users/davidsilva/Documents/agente/agent/convergence_daemon.log"
PYTHON="/Users/davidsilva/Documents/agente/ingest/.venv/bin/python"
WORKDIR="/Users/davidsilva/Documents/agente"

if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') — convergence daemon already running (pid $(cat "$LOCKFILE")), skipping" \
        >> "$LOGFILE"
    exit 0
fi

cd "$WORKDIR"

echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# 5-min polling: 30 cycles = 150 min (2.5 hours)
"$PYTHON" agent/convergence_trader.py \
    --cycles 30 \
    --interval 300 \
    >> "$LOGFILE" 2>&1
