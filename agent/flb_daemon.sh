#!/usr/bin/env bash
# flb_daemon.sh — run the FLB scanner + eval on a real 2-hour cadence.
#
# Why this exists: this Mac sleeps in a ~17min-sleep / ~3min-darkwake cycle all
# day, and macOS cron does NOT run jobs it missed while asleep. The old crons
# (`0 */2` scanner, `30 */2` eval) fired on the exact hour, so on 2026-07-23 the
# scanner ran 1 time out of 8 — every afternoon slot, the productive ones, fell
# inside a sleep window.
#
# So cron calls this every 10 minutes instead and the wrapper decides whether
# enough time has passed. Any darkwake window is now enough to catch up, and the
# cadence stays ~2h rather than becoming 10 minutes.
#
# Both stamp and lock live in the agent dir so a wipe of /tmp cannot silently
# turn this into a 10-minute scanner.
cd "$(dirname "$0")" || exit 1

MIN_GAP_MIN=110          # 110 not 120: a 2h cadence checked every 10min would
                         # otherwise drift to 130min (the check lands just short)
STAMP=".flb_last_run"
LOCK=".flb_daemon.lock"

# mkdir is atomic — two overlapping cron ticks cannot both get the lock.
if ! mkdir "$LOCK" 2>/dev/null; then
  # A lock older than 30min means the previous run died without cleaning up
  # (system slept mid-run). Reclaim it rather than jamming forever.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# -mmin +N is "modified more than N minutes ago"; a missing stamp runs now.
if [ -f "$STAMP" ] && [ -z "$(find "$STAMP" -maxdepth 0 -mmin +$MIN_GAP_MIN)" ]; then
  exit 0
fi

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate

echo "$(date -u +%FT%TZ) --- flb_daemon tick ---" >> flb_scanner.log
python flb_scanner.py >> flb_scanner.log 2>&1
python flb_eval.py --write >> flb_eval.log 2>&1

# Stamped only after the work, so a run killed by sleep is retried in 10min
# instead of being counted as done.
touch "$STAMP"
