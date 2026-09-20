"""One api-football key, two daemons, and until now no way to count the spend.

Every blackout in this repo has ended at the same dead end: api-football says
"you have reached the request limit for the day" while its own headers say
74,999 of 75,000 are free, and the only counter-argument we could offer was a
number scraped out of a log file after the fact. On 2026-09-05 the recorded
observations proved the refusal resets at 00:00 UTC — four consecutive nights,
first row back at 00:02-00:03 — so it IS a daily allowance. Whose, we still
cannot say, because we have never counted our own calls as we made them.

So this module does two things, and they are deliberately separate:

  1. COUNT.  Every call goes through `record()`. The tally is per UTC day and
     per hour, so tomorrow the question "did we spend 75,000?" is answered by a
     file rather than by parsing `ok=` out of a log.

  2. RATION.  The allowance is spent by whoever asks first, and the asking is
     flat across the day while the VALUE of a call is not: the European evening
     is where the boards we can trade actually are. Today's blackout began at
     16:11 UTC — the exact minute that window opens — after a day of enriching
     fixtures Polymarket does not list. `research_allowed()` is what stops that.

Two processes share the key, so each writes its OWN file and reads the other's.
A single writer per file needs no lock; a shared file would need one, and a
lock this repo forgets to release is a documented failure mode, not a
hypothetical (see the pressure daemon's `pgrep` guard, and the mkdir lock that
replaced it).
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── the allowance, and how it is meant to be spent ───────────────────────────
DAILY_LIMIT = 75_000

# The window we are protecting: European evening kickoffs, in UTC. Everything
# before it is preparation; everything inside it is the only time a board we
# can trade is live. 2026-09-05 went dark at 16:11Z, one hour in.
EVENING_START_H = 15

# Calls that must still be unspent when the evening opens. The daytime therefore
# gets DAILY_LIMIT - EVENING_RESERVE, spread evenly across its hours so the
# small hours cannot eat the afternoon either.
EVENING_RESERVE = 45_000

# Research = a fixture Polymarket does not list. It can never be traded; it only
# ever feeds the forward fit of the pressure model. On 2026-09-05 it was 89% of
# everything we recorded (39,974 of 44,685 fixture-minutes, 712 fixtures against
# 78 listed), which makes it both the largest daytime cost and the only one that
# can be cut without losing a decision.
#
# It is SAMPLED rather than switched off, and sampled on the fixture id, so a
# match we follow is followed all the way through. A random per-cycle sample
# would keep the same call volume but shred the rolling windows it pays for —
# a fixture measured at 22' and 31' but not 25' has no usable 15-minute window,
# so that spend would buy nothing at all.
RESEARCH_DAYTIME_SAMPLE = 4      # keep 1 fixture in 4 before EVENING_START_H

_DIR = Path(__file__).resolve().parent
_STATE_PREFIX = ".af_calls_"


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_hour() -> int:
    return datetime.now(timezone.utc).hour


class CallCounter:
    """Per-process tally of api-football calls, persisted per UTC day.

    `name` picks the file, and there must be exactly one live process per name —
    that is what makes the lockless write safe.
    """

    def __init__(self, name: str, flush_every: int = 25):
        self.path = _DIR / f"{_STATE_PREFIX}{name}.json"
        self.flush_every = flush_every
        self._day = _utc_day()
        self._by_kind: Counter = Counter()
        self._by_hour: Counter = Counter()
        self._unflushed = 0
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            blob = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        # A file from an earlier UTC day is history, not a starting total.
        if blob.get("day") != self._day:
            return
        self._by_kind = Counter(blob.get("by_kind") or {})
        self._by_hour = Counter({int(k): v for k, v in (blob.get("by_hour") or {}).items()})

    def flush(self) -> None:
        blob = {
            "day": self._day,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": sum(self._by_kind.values()),
            "by_kind": dict(self._by_kind),
            "by_hour": {str(k): v for k, v in sorted(self._by_hour.items())},
        }
        # Atomic: a half-written counter read by the sibling process would be a
        # parse error at best and a wrong ration at worst.
        try:
            fd, tmp = tempfile.mkstemp(dir=str(_DIR), prefix=".af_tmp_")
            with os.fdopen(fd, "w") as fh:
                json.dump(blob, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass                      # counting must never take the agent down
        self._unflushed = 0

    # ── counting ─────────────────────────────────────────────────────────────
    def record(self, kind: str, n: int = 1) -> None:
        """One api-football request was just made. Call it at the call site."""
        day = _utc_day()
        if day != self._day:          # midnight UTC: the allowance resets, so do we
            self.flush()
            self._day, self._by_kind, self._by_hour = day, Counter(), Counter()
        self._by_kind[kind] += n
        self._by_hour[_utc_hour()] += n
        self._unflushed += n
        if self._unflushed >= self.flush_every:
            self.flush()

    @property
    def today(self) -> int:
        return sum(self._by_kind.values())

    def report(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in self._by_kind.most_common())
        return f"{self.today} calls today ({parts or 'none'})"


# ── one counter per process ──────────────────────────────────────────────────
# The lockless write is safe only while exactly one live process writes a given
# file. Three modes of the pressure agent run as separate processes — the
# forever daemon, the `--settle` cron line, and whatever is being tried by hand
# — and they used to be told apart by remembering to set an environment
# variable. Deriving the name from argv instead means the invariant holds
# whether or not anyone remembered.
_MODE_FLAGS = ("--settle", "--backfill-finals", "--report", "--once", "--dry-run")
_process_counter: "CallCounter | None" = None


def _sanitise(name: str) -> str:
    """The name becomes a filename, so an interactive `python -c` must not be
    able to write `.af_calls_-c.json` next to the real counters."""
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name).strip("_")
    return safe or "unknown"


def default_name() -> str:
    if os.getenv("AF_COUNTER_NAME"):
        return _sanitise(os.environ["AF_COUNTER_NAME"])
    base = Path(sys.argv[0]).stem
    base = base.replace("_observer", "").replace("_agent", "")
    base = _sanitise(base)
    for flag in _MODE_FLAGS:
        if flag in sys.argv[1:]:
            return f"{base}_{flag.lstrip('-').replace('-', '_')}"
    return base


def process_counter() -> "CallCounter":
    """The single counter for this process. Every call site shares it, so the
    file has one writer and the day's total is the whole of what we spent."""
    global _process_counter
    if _process_counter is None:
        _process_counter = CallCounter(default_name())
        # A short-lived process (the settle cron, a --once) seldom reaches the
        # `flush_every` calls a write waits for, so its spend was never written
        # at all. On 2026-09-14, the day the key ran out, the settle counter
        # still held Sunday's total.
        atexit.register(_flush_if_pending, _process_counter)
    return _process_counter


def _flush_if_pending(counter: "CallCounter") -> None:
    if counter._unflushed:
        counter.flush()


def siblings_today(exclude: str = "") -> int:
    """What every OTHER counted process has spent today.

    Read fresh on every call: the sibling is a different process and its file is
    the only channel between us.
    """
    day, total = _utc_day(), 0
    for path in _DIR.glob(f"{_STATE_PREFIX}*.json"):
        if exclude and path.name == f"{_STATE_PREFIX}{exclude}.json":
            continue
        try:
            blob = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if blob.get("day") == day:
            total += int(blob.get("total") or 0)
    return total


def spent_today(counter: "CallCounter | None" = None) -> int:
    """Every counted process's spend today, this one included."""
    if counter is None:
        return siblings_today()
    return counter.today + siblings_today(exclude=counter.path.name[len(_STATE_PREFIX):-5])


def daytime_ceiling(hour: int | None = None) -> int:
    """How much the whole day is allowed to have spent by the top of `hour`.

    Flat inside the evening: once the window we were saving for is open there is
    nothing left to save it from.
    """
    hour = _utc_hour() if hour is None else hour
    if hour >= EVENING_START_H:
        return DAILY_LIMIT
    return int((DAILY_LIMIT - EVENING_RESERVE) * (hour + 1) / EVENING_START_H)


def research_allowed(fixture_id: int, counter: "CallCounter | None" = None) -> bool:
    """May we spend a call on a fixture we can never trade?

    Yes in the evening — by then the reserve exists to be spent, and an unlisted
    0-0 teaches the model's fit exactly as much as a listed one. In the daytime,
    only for the deterministic sample, and only while the day is inside its
    running ceiling.
    """
    if _utc_hour() >= EVENING_START_H:
        return spent_today(counter) < DAILY_LIMIT
    if fixture_id % RESEARCH_DAYTIME_SAMPLE:
        return False
    return spent_today(counter) < daytime_ceiling()


_last_logged: dict[str, float] = {}


def log_status(logger, counter: "CallCounter | None" = None,
               tag: str = "", force: bool = False, every_s: int = 300) -> None:
    """Put the day's spend in the log, throttled.

    Every cycle would be 2,880 lines a day from the sweep alone; never would
    repeat the mistake this module exists to fix. `force` is for the cycles that
    matter most — a refusal, where the question "how much had WE spent when the
    door closed?" is the entire diagnosis and has never once been answerable.
    """
    import time as _time
    now = _time.monotonic()
    if not force and now - _last_logged.get(tag, 0) < every_s:
        return
    _last_logged[tag] = now
    if counter is not None:
        counter.flush()
        logger.info(f"{status_line(counter)} | this process: {counter.report()}")
    else:
        logger.info(status_line())


def status_line(counter: "CallCounter | None" = None) -> str:
    spent, hour = spent_today(counter), _utc_hour()
    ceiling = daytime_ceiling(hour)
    phase = "evening" if hour >= EVENING_START_H else "daytime"
    return (f"api-football budget: {spent}/{DAILY_LIMIT} today "
            f"({phase} {hour:02d}Z, ceiling {ceiling})")
