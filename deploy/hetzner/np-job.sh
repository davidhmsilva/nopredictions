#!/usr/bin/env bash
# np-job.sh — the one entry point systemd uses for every NOPREDICTIONS job.
#
# Each case below is a line of the Mac crontab (reports/crontab_backup_2026-09-19.txt)
# or a launchd daemon, with the same flags, the same AF_COUNTER_NAME and the same
# log file. The schedule moved into systemd timers (deploy/hetzner/systemd/), and
# cron_guard.sh is gone: it existed only because macOS cron skips jobs missed while
# asleep. A timer with Persistent=true catches up on its own, and a oneshot service
# never starts twice while a run is still going, so every-minute jobs cannot stack.
#
#   np-job.sh <job>          # what the units call
#   np-job.sh list           # the job names
#   np-job.sh py <script> …  # run any agent script with the env + venv loaded
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENT="$ROOT/agent"
INGEST="$ROOT/ingest"
SELF="$ROOT/deploy/hetzner/np-job.sh"
job="${1:-}"

if [ "$job" = "list" ]; then
  grep -oE '^  [a-z-]+\)' "$SELF" | tr -d ' )'; exit 0
fi
[ -f "$INGEST/.env" ] || { echo "missing $INGEST/.env" >&2; exit 1; }

# Same loading as the Mac wrappers: the .env is shell syntax, not systemd's
# EnvironmentFile syntax, so it is sourced rather than handed to systemd.
set +u; set -a; . "$INGEST/.env"; set +a
. "$INGEST/.venv/bin/activate"; set -u

cd "$AGENT" || exit 1

case "$job" in
  # ── daemons (Type=simple, Restart=always) ──────────────────────────────────
  pressure-daemon)
    # The wrapper, not python directly: pressure_health.py reads the heartbeat
    # stamp this wrapper writes, and would call the agent WEDGED without it.
    exec bash "$AGENT/pressure_daemon.sh" ;;
  settled-sweep)
    # flock singleton inside the observer; systemd does the restarting.
    AF_COUNTER_NAME=sweep exec python settled_sweep_observer.py --interval 30 \
      >> settled_sweep_observer.log 2>&1 ;;

  # ── periodic jobs (Type=oneshot, driven by a .timer) ────────────────────────
  pressure-settle)
    AF_COUNTER_NAME=pressure_settle exec python pressure_agent.py --settle >> pressure_agent.log 2>&1 ;;
  pressure-health)
    exec python pressure_health.py --quiet --recover >> pressure_health.log 2>&1 ;;
  sweep-settle)
    AF_COUNTER_NAME=sweep_settle exec python settled_sweep_observer.py --settle >> settled_sweep_observer.log 2>&1 ;;
  stage-a)
    cd "$INGEST" && exec python stage_a_football_data.py --current-season --refresh --continue-on-error \
      >> stage_a_cron.log 2>&1 ;;
  nfl-agent)
    exec python nfl_agent.py --once >> nfl_agent.log 2>&1 ;;
  nfl-live)
    exec python nfl_live_recorder.py --once >> nfl_live_recorder.log 2>&1 ;;
  factory-run)
    exec python factory_cli.py run >> factory.log 2>&1 ;;
  factory-settle)
    exec python factory_cli.py settle >> factory.log 2>&1 ;;
  factory-grid)
    exec python factory_cli.py grid --refresh --register >> factory_grid.log 2>&1 ;;
  soccer-live)
    exec python soccer_live_recorder.py --once >> soccer_live_recorder.log 2>&1 ;;
  soccer-live-settle)
    exec python soccer_live_recorder.py --settle >> soccer_live_recorder.log 2>&1 ;;
  lab-runner)
    exec python lab_strategy_runner.py --once >> lab_runner.log 2>&1 ;;
  lab-settle)
    exec python lab_strategy_runner.py --settle >> lab_runner.log 2>&1 ;;

  # ── by hand: any agent script with the server's env and venv ────────────────
  py)
    shift; exec python "$@" ;;

  *)
    echo "unknown job: '$job' (try: np-job.sh list)" >&2; exit 2 ;;
esac
