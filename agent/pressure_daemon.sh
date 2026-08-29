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
#
# It went dark for 36h on 2026-08-27 with the lock held and NOBODY stacked --
# the opposite failure, and the one D1-D4 cannot see:
#
#  D5. `kill -0 $holder` is a liveness check, not a health check. A macOS
#      per-process permission revocation (the EPERM that kills every outbound
#      socket) kills this wrapper's DISK access too: pid 75752 spun `while true`
#      for 36h starting a python that could not read its own script, and could
#      not write one log line about it. It answered `kill -0` the whole time, so
#      all 216 cron ticks exited silently and the agent stayed dead. Now the lock
#      carries a heartbeat: the holder must keep touching the pid file, and a
#      lock untouched for STALE_MIN is taken over -- by killing the holder first,
#      because leaving it running is D3 all over again. The two checks are
#      complementary and BOTH must pass: `kill -0` catches a dead holder whose
#      heartbeat was orphaned, the heartbeat catches a holder that is alive but
#      dead inside.
#
#  D6. The D5 heartbeat lived in /tmp and did not catch the NEXT occurrence
#      (2026-08-28 22:45 -> 08-29 12:03, 13.3h dark). The revocation is not
#      "the process lost the network", it is "the process lost ~/Documents":
#      pid 37045 could still write /tmp every 30s while it could not read
#      pressure_agent.py or append one line to pressure_agent.log. A liveness
#      token has to live on the resource whose loss you are trying to detect,
#      so the heartbeat is now a stamp in the AGENT DIRECTORY, next to the log.
#      Two things faked freshness and both are fixed: the stamp moved out of
#      /tmp, and the restart loop's own `echo $$ > $PID_FILE` no longer counts
#      as a heartbeat (a crash-loop refreshed it every 30s forever).
#
#      ⚠️ The heartbeat proves the SUPERVISOR can still act. It cannot prove the
#      agent is producing anything — a python that dies instantly still leaves a
#      healthy-looking wrapper. That second question is only answerable from
#      `max(observed_at)` in the tables; see agent/pressure_health.py.
cd "$(dirname "$0")"

LOCK_DIR="/tmp/nopredictions_pressure.lock"
PID_FILE="$LOCK_DIR/pid"
# The lock is only honoured while its holder keeps proving it can still write to
# disk (D5). 30s against a 5min window is a 10x margin, so a slow machine or a
# missed tick never costs us the lock.
HEARTBEAT_S=30
STALE_MIN=5
# Deliberately NOT inside $LOCK_DIR: /tmp stays writable when the TCC grant on
# ~/Documents is revoked, which is the exact failure this has to catch (D6).
# Touching a file here proves the holder can still reach the code and the log.
HEARTBEAT_FILE=".pressure_heartbeat"
# Anchored on --interval so the --settle cron run is never matched (D1).
AGENT_PAT="pressure_agent.py --interval"

log() { echo "$(date -u +%FT%TZ) [daemon] $*" >> pressure_agent.log; }

# Fresh = the holder touched the pid file within STALE_MIN. `find -mmin +N`
# prints the file only when it IS older, so empty output means fresh (D5).
lock_is_fresh() {
  [ -f "$HEARTBEAT_FILE" ] && \
    [ -z "$(find "$HEARTBEAT_FILE" -maxdepth 0 -mmin "+$STALE_MIN" 2>/dev/null)" ]
}

# Runs in the background for as long as its wrapper lives. It is a fork of the
# wrapper, so its `touch` fails in exactly the conditions the wrapper's own disk
# access fails -- which is the entire point: a supervisor that can no longer
# write must stop looking alive. It watches the wrapper rather than being waited
# on by it, so it cannot outlive its owner and keep a dead lock warm.
heartbeat() {
  # Two independent stop conditions: owner gone, or the lock is no longer ours.
  # The second matters after a takeover -- an orphan that keeps touching a
  # stranger's lock would make a corpse look alive, which is the bug D5 fixes.
  while kill -0 "$1" 2>/dev/null && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$1" ]; do
    touch "$HEARTBEAT_FILE" 2>/dev/null
    sleep "$HEARTBEAT_S"
  done
}

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
  # Alive AND still beating is the only state that keeps us out (D5).
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && lock_is_fresh; then
    exit 0
  fi
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    # Wedged, not gone. It must die before we take over or we are back to two
    # wrappers writing every observation twice (D3). TERM is deferred until its
    # in-flight `sleep` returns, and 2026-08-25 showed one that ignored TERM
    # entirely, so escalate rather than wait for a cron tick that never wins.
    log "holder $holder alive but heartbeat stale (>${STALE_MIN}min) — killing it and taking over"
    # Its children go with it. A heartbeat that is stopped (or wedged) never
    # re-evaluates its stop conditions, so it would outlive the holder and keep
    # touching the lock for a corpse -- verified: this leaked on the first pass.
    # SIGCONT first, or a SIGSTOPped process never processes the TERM at all.
    holder_kids="$(pgrep -P "$holder" 2>/dev/null)"
    kill -CONT "$holder" $holder_kids 2>/dev/null
    kill "$holder" $holder_kids 2>/dev/null
    sleep 5
    kill -9 "$holder" $holder_kids 2>/dev/null
  else
    log "stale lock from pid ${holder:-none} — taking over"
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" 2>/dev/null || exit 0
fi
# Stamp first, pid second: a cron tick that catches us mid-acquire must never
# see a pid with no stamp and read that as a wedged holder.
touch "$HEARTBEAT_FILE" 2>/dev/null
echo $$ > "$PID_FILE"
heartbeat $$ &
HEARTBEAT_PID=$!
# Kill the heartbeat BEFORE releasing, so it can never re-touch a lock we just
# gave up (touch would recreate the pid file under a stranger's lock dir).
trap 'kill "$HEARTBEAT_PID" 2>/dev/null; release_lock' EXIT
trap 'kill "$HEARTBEAT_PID" 2>/dev/null; release_lock; kill "$AGENT_PID" 2>/dev/null; exit 0' INT TERM

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
  # Ownership only — NOT a liveness signal. This line refreshing the old /tmp
  # heartbeat every 30s is what let a crash-loop look healthy for 13h (D6).
  echo $$ > "$PID_FILE"
  sleep 30
done
