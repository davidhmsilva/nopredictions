#!/usr/bin/env bash
# settled_sweep_daemon.sh — keep settled_sweep_observer.py alive 24/7.
#
# Observation only. The observer places no orders and holds no keys.
#
# The singleton guard lives in PYTHON here, not in this wrapper: the observer
# takes an flock on /tmp/nopredictions_settled_sweep.lock and the kernel releases
# it however the process dies. That is strictly better than the mkdir lock the
# sibling wrappers use (which leaks its directory on kill -9, and needs the
# stale-pid dance below to recover) and than a pgrep guard, which is not a guard
# at all — the pressure agent's pgrep singleton also matched its own `--settle`
# cron, freed a lock it did not own, and stacked 22 hours of duplicate rows.
#
# So this wrapper only has to survive being re-run: a second copy starts, fails
# to take the flock, logs one line and exits.
cd "$(dirname "$0")"

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate

while true; do
  python settled_sweep_observer.py --interval 30 >> settled_sweep_observer.log 2>&1
  echo "$(date -u +%FT%TZ) observer exited — restarting in 30s" >> settled_sweep_observer.log
  sleep 30
done
