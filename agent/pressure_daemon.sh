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
#
# It stacked anyway on 2026-08-14 16:00 — two wrappers, two agents, every
# observation written ~2.5x for 22h. Four defects let a second loop in, all
# closed below. Read this before "simplifying" any of it:
#
#  D1. The old `pgrep -f "pressure_agent.py"` guard ALSO matched the `--settle`
#      cron run (separate crontab line, every ~110min). A wrapper firing during
#      a settle exited on that check — and the exit came AFTER the trap was
#      armed, so it deleted a lock it did not own. Now: no pgrep gate at all
#      (the lock is the gate), and every pattern is anchored on `--interval`
#      so a settle run can never be mistaken for the forever-agent.
#  D2. `trap 'rm -rf $LOCK_DIR'` fired unconditionally, so ANY path out of this
#      script destroyed whatever lock was on disk, including another wrapper's.
#      Now the trap releases only if the pid file still names us.
#  D3. A wrapper killed mid-flight left its agent running unsupervised; the next
#      wrapper saw it via pgrep and exited, so the orphan lived forever with
#      nobody to restart it. Now the lock holder reaps any agent that is not its
#      own child before starting one.
#  D4. `mkdir` and the pid write were not atomic together: a wrapper that lost
#      the mkdir race but read the pid file before the winner wrote it saw no
#      pid, called the lock stale, and took over. Now a pid-less lock is treated
#      as a wrapper still starting up, not as a stale one.
cd "$(dirname "$0")"

LOCK_DIR="/tmp/nopredictions_pressure.lock"
PID_FILE="$LOCK_DIR/pid"
# Anchored on --interval so the --settle cron run is never matched (D1).
AGENT_PAT="pressure_agent.py --interval"

log() { echo "$(date -u +%FT%TZ) [daemon] $*" >> pressure_agent.log; }

# Release only a lock we still own, so we can never free another wrapper's (D2).
release_lock() {
  if [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ]; then
    rm -rf "$LOCK_DIR"
  fi
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  holder="$(cat "$PID_FILE" 2>/dev/null)"
  if [ -z "$holder" ]; then
    # No pid yet. Either the winner of the mkdir race has not written it (a
    # sub-second window) or a wrapper died between the two. Give it a moment;
    # only a lock that is still pid-less AND old is genuinely abandoned (D4).
    sleep 2
    holder="$(cat "$PID_FILE" 2>/dev/null)"
    if [ -z "$holder" ] && [ -z "$(find "$LOCK_DIR" -maxdepth 0 -mmin +5 2>/dev/null)" ]; then
      exit 0
    fi
  fi
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    exit 0
  fi
  log "stale lock from pid ${holder:-none} — taking over"
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" 2>/dev/null || exit 0
fi
echo $$ > "$PID_FILE"
trap 'release_lock' EXIT
trap 'release_lock; kill "$AGENT_PID" 2>/dev/null; exit 0' INT TERM

# We hold the lock, so any forever-agent alive right now is an orphan from a
# wrapper that was killed without cleaning up. Reap it — leaving it running is
# what produced the double-write (D3).
for orphan in $(pgrep -f "$AGENT_PAT" 2>/dev/null); do
  log "reaping orphan agent pid $orphan"
  kill "$orphan" 2>/dev/null
done

set -a; source ../ingest/.env; set +a
source ../ingest/.venv/bin/activate
while true; do
  python pressure_agent.py --interval 60 >> pressure_agent.log 2>&1 &
  AGENT_PID=$!
  wait "$AGENT_PID"
  log "pressure agent exited — restarting in 30s"
  # Re-assert ownership: if anything cleared the lock while we were running, we
  # are still the live wrapper and must hold it across the restart sleep, which
  # is precisely the window a cron tick used to slip through.
  [ -d "$LOCK_DIR" ] || mkdir "$LOCK_DIR" 2>/dev/null
  echo $$ > "$PID_FILE"
  sleep 30
done
