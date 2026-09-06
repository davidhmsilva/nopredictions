#!/usr/bin/env bash
# late_goals_daemon.sh — keep late_goals_observer.py alive 24/7.
#
# Paper only: the observer records PM live over prices against the empirical
# late-goal fair value and never places an order. Kickoffs span every timezone
# and the universe refresh is cheap, so it just runs always — same shape as
# tick_daemon.sh.
#
# Cron re-runs this hourly so a crash self-heals, which means it MUST be a
# singleton — without the guard every hour would stack another forever-loop and
# multiply the writes.
#
# Guarding on the observer alone is not enough and did not hold: this wrapper
# sleeps 30s between restarts, and a cron firing inside that window sees no
# observer, passes the check, and stacks a second forever-loop. Found on
# 2026-08-04 with THREE observers live and 682 poll-groups written 2-3 times.
# So the wrapper guards on itself first, with an atomic directory lock — mkdir
# either wins or fails, with no window between testing and taking it.
cd "$(dirname "$0")"

LOCK_DIR="/tmp/nopredictions_late_goals.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # A stale lock outlives a kill -9. If the recorded pid is gone, take it over.
  if [ -f "$LOCK_DIR/pid" ] && kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
    exit 0
  fi
  echo "$(date -u +%FT%TZ) stale lock from pid $(cat "$LOCK_DIR/pid" 2>/dev/null) — taking over" \
    >> late_goals_observer.log
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

if pgrep -f "late_goals_observer.py" > /dev/null; then
  exit 0
fi

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate
while true; do
  python late_goals_observer.py --interval 60 >> late_goals_observer.log 2>&1
  echo "$(date -u +%FT%TZ) observer exited — restarting in 30s" >> late_goals_observer.log
  sleep 30
done
