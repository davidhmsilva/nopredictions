#!/usr/bin/env bash
# cron_guard.sh — make a cron job survive this machine's sleep cycle.
#
# THE PROBLEM: this Mac sleeps in a ~17min-sleep / ~3min-darkwake cycle all day,
# and macOS cron does NOT run jobs it missed while asleep (unlike launchd). A job
# scheduled at an exact time — `0 8`, `0 7`, `0 6,12,18,23` — therefore fires only
# if the machine happens to be awake at that instant, roughly 15% of the time.
# Measured on 2026-07-23: flb_scanner ran 1 time in 8, paper_trader had not run
# since 2026-07-20, drift_scanner missed both of its daytime slots. No errors in
# any log — the jobs were simply never invoked, which is why this went unnoticed.
#
# THE FIX: cron calls this every 10 minutes and the guard decides whether the job
# is actually due. Any darkwake window is now enough to catch up, while the
# intended cadence is preserved.
#
#   cron_guard.sh --gap 110  <slug> <command...>   # >= N minutes since last run
#   cron_guard.sh --daily 08:00 <slug> <command...># once per calendar day, at/after HH:MM
#
# <slug> names the stamp file, so it must be unique per job (two jobs sharing a
# slug would starve each other).
#
# Notes for whoever edits this next:
#  - Use a --gap slightly BELOW the true target (110 for a 2h cadence). Checked
#    every 10min, a gap of exactly 120 misses the tick at 119min and the cadence
#    silently drifts to 130min.
#  - The stamp is written AFTER the command, so a run killed mid-flight by sleep
#    is retried on the next tick rather than counted as done.
#  - State lives in agent/.cron_guard/ and not /tmp, because a /tmp wipe would
#    silently turn every guarded job into a 10-minute job.

set -u

usage() { echo "usage: cron_guard.sh (--gap MINUTES | --daily HH:MM) <slug> <command...>" >&2; exit 2; }

[ $# -ge 3 ] || usage
MODE="$1"; ARG="$2"; SLUG="$3"; shift 3
[ $# -ge 1 ] || usage

case "$MODE" in
  --gap)   [[ "$ARG" =~ ^[0-9]+$ ]] || usage ;;
  --daily) [[ "$ARG" =~ ^[0-9]{2}:[0-9]{2}$ ]] || usage ;;
  *) usage ;;
esac

cd "$(dirname "$0")" || exit 1
STATE=".cron_guard"
mkdir -p "$STATE"
STAMP="$STATE/$SLUG.stamp"
LOCK="$STATE/$SLUG.lock"

# mkdir is atomic — two overlapping ticks cannot both win the lock.
if ! mkdir "$LOCK" 2>/dev/null; then
  # A lock older than 6h means a previous run died without cleaning up (killed
  # mid-flight by sleep). Reclaim it, or the job jams forever. 6h is chosen to be
  # longer than the slowest guarded job by a wide margin.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

due=1
if [ "$MODE" = "--gap" ]; then
  # -mmin +N is "modified more than N minutes ago". No stamp => first run => due.
  if [ -f "$STAMP" ] && [ -z "$(find "$STAMP" -maxdepth 0 -mmin +"$ARG" 2>/dev/null)" ]; then
    due=0
  fi
else
  # Once per calendar day, and never before the nominal time. The time-of-day
  # test must sit OUTSIDE the stamp test: on the very first run there is no
  # stamp, and skipping the check then would fire the job at whatever time cron
  # first called us.
  [ "$(date +%H:%M)" \< "$ARG" ] && due=0
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$(date +%F)" ]; then
    due=0
  fi
fi
[ "$due" = "1" ] || exit 0

"$@"
rc=$?

if [ "$MODE" = "--daily" ]; then
  date +%F > "$STAMP"
else
  touch "$STAMP"
fi
exit $rc
