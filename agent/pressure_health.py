#!/usr/bin/env python3
"""pressure_health.py — is the pressure agent actually WORKING?

A running process proves nothing here. Three times in three weeks the daemon has
been alive, logging, holding its lock, and producing nothing:

  2026-08-20→25  stats frozen, index pinned at 5.0      (fabricated rows)
  2026-08-27→28  EPERM on sockets, wrapper spun mute    (36h dark)
  2026-08-28→29  EPERM on ~/Documents, /tmp still fine  (13h dark)

Every one of them was found by hand, days late, and in the last two the log had a
FRESH mtime the whole time because `--settle` is a separate cron line writing to
the same file. The only signal that has never lied is `max(observed_at)` in the
observation tables — so that is what this checks.

Three verdicts, because the remedies differ:

  WEDGED      the wrapper's heartbeat stamp is stale -> it cannot write the agent
              directory any more. Nothing it does will fix itself; the lock
              holder has to be killed so a cron tick can take over. (--recover)
  CRASH-LOOP  stamp fresh, but no agent process -> the supervisor is fine and
              python is dying on every restart. Read the log, do not restart.
  STALE       stamp fresh, agent alive, but no rows for a while -> either a
              silent hang or a genuinely quiet night. Warning, not an alarm.

⚠️ SLEEP IS NOT AN OUTAGE. This Mac sleeps ~22% of the day and macOS cron does
not run what it missed (see cron_guard.sh). In a known-healthy window, 22-26 Aug,
the gaps between minutes-with-writes were p50 1min / p99 2min — but the tail held
gaps of 45, 193, 345 and 457 minutes, and those are sleep, not failure. A naive
"no rows for 30 min" alarm would fire on every one of them. So staleness is only
counted while the machine was CONTINUOUSLY AWAKE, which this measures by its own
tick: if the previous run was longer ago than SLEEP_GAP_MIN, the machine slept in
between and the span starts over.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.db import _conn  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP_FILE = os.path.join(HERE, ".pressure_heartbeat")
STATE_FILE = os.path.join(HERE, ".cron_guard", "pressure_health.state")
LOG_FILE = os.path.join(HERE, "pressure_health.log")
LOCK_PID = "/tmp/nopredictions_pressure.lock/pid"

TABLES = ("pressure_observations", "ht_pressure_observations", "fav_ht_observations")

# Awake staleness before we call it. p99 of healthy awake gaps is 2 min, so 20 is
# ~10x the normal worst case and still catches an outage inside half an hour.
STALE_MIN = 20
# The wrapper touches its stamp every 30s. Same 5-min window it uses itself.
STAMP_STALE_MIN = 5
# Two ticks of a */10 cron. A longer gap than this means we were not running,
# which on this machine means the machine was asleep.
SLEEP_GAP_MIN = 15


def _age_min(path: str) -> float | None:
    try:
        return (datetime.now().timestamp() - os.stat(path).st_mtime) / 60
    except OSError:
        return None


def _agent_running() -> bool:
    # Anchored on --interval so the --settle cron run is never mistaken for the
    # forever-agent — the same trap that cost pressure_daemon.sh its D1.
    return subprocess.run(
        ["pgrep", "-f", "pressure_agent.py --interval"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _last_observation() -> tuple[datetime | None, dict[str, datetime | None]]:
    per: dict[str, datetime | None] = {}
    with _conn() as conn:
        cur = conn.cursor()
        for t in TABLES:
            cur.execute(f"SELECT max(observed_at) FROM {t}")
            per[t] = cur.fetchone()[0]
    seen = [v for v in per.values() if v is not None]
    return (max(seen) if seen else None), per


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh)


def _notify(title: str, message: str) -> None:
    # Best-effort. A notification nobody is there to read is not the mechanism —
    # the log line and the exit code are. This just shortens the feedback loop
    # when someone IS at the machine.
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def check(recover: bool = False, quiet: bool = False) -> int:
    now = datetime.now(timezone.utc)
    state = _load_state()
    prev_tick = state.get("last_tick")
    prev_tick_dt = datetime.fromisoformat(prev_tick) if prev_tick else None

    # Did we run recently enough to claim the machine has been awake throughout?
    awake_span = (
        prev_tick_dt is not None
        and (now - prev_tick_dt) <= timedelta(minutes=SLEEP_GAP_MIN)
    )

    stamp_age = _age_min(STAMP_FILE)
    agent_up = _agent_running()
    last_obs, per_table = _last_observation()
    obs_age = (now - last_obs).total_seconds() / 60 if last_obs else None

    verdict, detail = "OK", ""
    if stamp_age is None:
        verdict = "WEDGED"
        detail = "heartbeat stamp missing — the daemon has never held the lock cleanly"
    elif stamp_age > STAMP_STALE_MIN:
        verdict = "WEDGED"
        detail = (f"heartbeat stamp {stamp_age:.0f}min old (>{STAMP_STALE_MIN}) — the "
                  f"wrapper can no longer write the agent directory")
    elif not agent_up:
        verdict = "CRASH-LOOP"
        detail = "supervisor healthy but no `--interval` agent process — python is dying on restart"
    elif obs_age is None:
        verdict = "STALE"
        detail = "no observations have ever been recorded"
    elif obs_age > STALE_MIN:
        # Only a span we watched go stale WHILE AWAKE counts. A first stale tick,
        # or one whose predecessor sits on the far side of a sleep, opens a span
        # and says so — it does not alarm.
        since = state.get("stale_since") if awake_span else None
        span = (now - datetime.fromisoformat(since)).total_seconds() / 60 if since else 0.0
        if span > STALE_MIN:
            verdict = "STALE"
            detail = (f"no rows for {obs_age:.0f}min, and we have been awake watching "
                      f"it go stale for {span:.0f}min")
        else:
            detail = (f"no rows for {obs_age:.0f}min — opening a span "
                      f"({'awake' if awake_span else 'machine slept since last check'})")

    # Span bookkeeping: cleared the moment rows come back.
    if obs_age is not None and obs_age <= STALE_MIN:
        state.pop("stale_since", None)
    elif "stale_since" not in state:
        state["stale_since"] = now.isoformat()

    alarm = verdict != "OK"
    stamp_s = "MISSING" if stamp_age is None else f"{stamp_age:.1f}min"
    obs_s = "never" if obs_age is None else f"{obs_age:.1f}min"
    line = (f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} [health] {verdict:11s}"
            f" stamp={stamp_s} agent={'up' if agent_up else 'DOWN'} last_obs={obs_s}")
    if detail:
        line += f" | {detail}"

    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass                      # if we cannot write here either, stdout still carries it
    if not quiet:
        print(line)
        if alarm:
            for t, v in per_table.items():
                print(f"    {t:26s} {v}")

    # Notify only on the transition into alarm, so a long outage is one ping.
    if alarm and state.get("last_verdict") != verdict:
        _notify(f"NOPREDICTIONS: pressure agent {verdict}", detail or line)
    state["last_verdict"] = verdict
    state["last_tick"] = now.isoformat()
    _save_state(state)

    if recover and verdict == "WEDGED":
        holder = None
        try:
            with open(LOCK_PID) as fh:
                holder = fh.read().strip()
        except OSError:
            pass
        if holder and holder.isdigit():
            kids = subprocess.run(["pgrep", "-P", holder], capture_output=True, text=True)
            pids = [holder] + kids.stdout.split()
            # SIGCONT first: a stopped process never processes the TERM at all.
            for sig in ("-CONT", "-TERM"):
                subprocess.run(["kill", sig] + pids,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"    recover: signalled wedged holder {holder} (+{len(pids)-1} children); "
                  f"launchd KeepAlive restarts the wrapper in ~30s")

    return 1 if alarm else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recover", action="store_true",
                    help="on WEDGED, kill the lock holder so a cron tick can take over")
    ap.add_argument("--quiet", action="store_true", help="log only, no stdout")
    args = ap.parse_args()
    return check(recover=args.recover, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
