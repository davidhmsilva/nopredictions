#!/usr/bin/env bash
# pressure_daemon.sh — keep pressure_agent.py alive while the machine is awake.
#
# Paper only. The agent records in-game pressure against the empirical goal base
# rate and opens 1u paper positions on PM over lines; it never places an order.
#
# Cron re-runs this every 10 minutes so a crash self-heals, which means it MUST
# be a singleton — otherwise every firing stacks another forever-loop and the
# same fixture gets written several times a cycle. That exact failure happened
# to the late-goals observer on 2026-08-04 (three observers live, 682 poll
# groups written 2-3x), and guarding on the python process alone is what let it
# through: this wrapper sleeps between restarts, and a cron firing inside that
# window sees no agent, passes the check, and starts a second loop. So the lock
# is an atomic mkdir on the wrapper itself, taken before anything else.
cd "$(dirname "$0")"

LOCK_DIR="/tmp/nopredictions_pressure.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # A stale lock outlives a kill -9. If the recorded pid is gone, take it over.
  if [ -f "$LOCK_DIR/pid" ] && kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
    exit 0
  fi
  echo "$(date -u +%FT%TZ) stale lock from pid $(cat "$LOCK_DIR/pid" 2>/dev/null) — taking over" \
    >> pressure_agent.log
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

if pgrep -f "pressure_agent.py" > /dev/null; then
  exit 0
fi

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate
while true; do
  python pressure_agent.py --interval 60 >> pressure_agent.log 2>&1
  echo "$(date -u +%FT%TZ) pressure agent exited — restarting in 30s" >> pressure_agent.log
  sleep 30
done
