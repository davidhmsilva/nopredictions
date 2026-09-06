"""
Tests for the api-football daily allowance: counting it, and rationing it.

Four blackouts in six days were all diagnosed after the fact from `ok=` counts
scraped out of a log, and the argument always stalled in the same place — we
could not say what we had actually spent. So the counter is the point here, and
the rationing is only as trustworthy as the counter under it.

The rationing rule these tests pin down: research (a fixture Polymarket does not
list, which therefore can never be traded) is sampled during the day so the
allowance survives into the European evening, and the sample is DETERMINISTIC on
the fixture id. A random sample would cut the same number of calls and destroy
what they buy — the pressure arms difference a 15-minute rolling window, and a
fixture measured at 22' and 31' but not at 25' has no window at all.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_af_budget.py -q
"""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import af_budget as ab  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the real counters — a test that writes them would corrupt
    the production ration for the rest of the UTC day."""
    monkeypatch.setattr(ab, "_DIR", tmp_path)
    yield tmp_path


def _at_hour(monkeypatch, hour: int):
    monkeypatch.setattr(ab, "_utc_hour", lambda: hour)


# ── the ceiling curve ────────────────────────────────────────────────────────

def test_daytime_ceiling_rises_to_the_reserve_and_no_further():
    """By the last daytime hour the day may have spent everything that is not
    reserved — and not one call more."""
    assert ab.daytime_ceiling(ab.EVENING_START_H - 1) == ab.DAILY_LIMIT - ab.EVENING_RESERVE


def test_daytime_ceiling_is_monotone_and_starts_small():
    ceilings = [ab.daytime_ceiling(h) for h in range(ab.EVENING_START_H)]
    assert ceilings == sorted(ceilings)
    assert ceilings[0] < ab.DAILY_LIMIT - ab.EVENING_RESERVE


def test_evening_ceiling_is_the_whole_allowance():
    """Once the window we were saving for is open, there is nothing left to save
    it from."""
    for h in range(ab.EVENING_START_H, 24):
        assert ab.daytime_ceiling(h) == ab.DAILY_LIMIT


# ── the research sample ──────────────────────────────────────────────────────

def test_research_is_sampled_in_the_daytime(monkeypatch):
    _at_hour(monkeypatch, 10)
    kept = [fid for fid in range(1000) if ab.research_allowed(fid)]
    assert len(kept) == pytest.approx(1000 / ab.RESEARCH_DAYTIME_SAMPLE, rel=0.02)


def test_the_daytime_sample_is_stable_for_a_given_fixture(monkeypatch):
    """The whole point. A fixture kept at 22' must still be kept at 25' and 31',
    or the rolling window it is paying for never closes."""
    _at_hour(monkeypatch, 9)
    for fid in range(200):
        verdicts = {ab.research_allowed(fid) for _ in range(5)}
        assert len(verdicts) == 1


def test_research_is_unrestricted_in_the_evening(monkeypatch):
    _at_hour(monkeypatch, ab.EVENING_START_H)
    assert all(ab.research_allowed(fid) for fid in range(200))


def test_research_stops_at_the_ceiling_even_for_the_sample(monkeypatch):
    """Being in the sample is permission to spend, not permission to overspend."""
    _at_hour(monkeypatch, 6)
    sampled = next(f for f in range(100) if f % ab.RESEARCH_DAYTIME_SAMPLE == 0)
    assert ab.research_allowed(sampled)

    counter = ab.CallCounter("hog")
    counter.record("stats", ab.daytime_ceiling(6) + 1)
    counter.flush()
    assert not ab.research_allowed(sampled)


def test_the_evening_still_stops_at_the_daily_limit(monkeypatch):
    _at_hour(monkeypatch, 20)
    counter = ab.CallCounter("hog")
    counter.record("stats", ab.DAILY_LIMIT)
    counter.flush()
    assert not ab.research_allowed(0)


# ── the counter ──────────────────────────────────────────────────────────────

def test_the_counter_survives_a_restart(isolated_state):
    a = ab.CallCounter("pressure", flush_every=1)
    a.record("live"); a.record("stats", 4)
    assert ab.CallCounter("pressure").today == 5


def test_a_counter_from_an_earlier_day_is_history_not_a_starting_total(isolated_state):
    stale = {"day": "1999-01-01", "total": 70000,
             "by_kind": {"stats": 70000}, "by_hour": {"12": 70000}}
    (isolated_state / f"{ab._STATE_PREFIX}pressure.json").write_text(json.dumps(stale))
    assert ab.CallCounter("pressure").today == 0


def test_midnight_utc_resets_the_tally_mid_process(isolated_state, monkeypatch):
    """The allowance resets at 00:00 UTC — measured, four nights running — and a
    process that runs for weeks has to reset with it rather than carry
    yesterday's spend into today's ration."""
    counter = ab.CallCounter("pressure", flush_every=1)
    counter.record("stats", 500)
    monkeypatch.setattr(ab, "_utc_day", lambda: "2999-12-31")
    counter.record("stats")
    assert counter.today == 1


def test_the_two_daemons_see_each_others_spend(isolated_state):
    """One key, two processes. Each writes its own file precisely so neither
    needs a lock; the ration is only correct if each also READS the other."""
    pressure = ab.CallCounter("pressure", flush_every=1)
    sweep = ab.CallCounter("sweep", flush_every=1)
    pressure.record("stats", 300)
    sweep.record("ids", 700)
    assert ab.spent_today(pressure) == 1000
    assert ab.spent_today(sweep) == 1000


def test_a_corrupt_counter_file_does_not_take_the_agent_down(isolated_state):
    (isolated_state / f"{ab._STATE_PREFIX}sweep.json").write_text("{not json")
    counter = ab.CallCounter("pressure", flush_every=1)
    counter.record("live")
    assert ab.spent_today(counter) == 1


# ── one writer per file ──────────────────────────────────────────────────────

@pytest.mark.parametrize("argv,expected", [
    (["pressure_agent.py", "--interval", "60"], "pressure"),
    (["pressure_agent.py", "--settle"], "pressure_settle"),
    (["pressure_agent.py", "--report"], "pressure_report"),
    (["settled_sweep_observer.py", "--interval", "30"], "settled_sweep"),
    (["settled_sweep_observer.py", "--once", "--dry-run"], "settled_sweep_once"),
])
def test_each_mode_gets_its_own_counter_file(monkeypatch, argv, expected):
    """The forever daemon and the --settle cron line are different processes
    sharing one key. A lockless write is only safe while one process writes a
    given file, and remembering to set an environment variable is not a
    mechanism — so the name comes off argv."""
    monkeypatch.delenv("AF_COUNTER_NAME", raising=False)
    monkeypatch.setattr(ab.sys, "argv", argv)
    assert ab.default_name() == expected


def test_an_interactive_run_cannot_scribble_on_the_real_counters(monkeypatch):
    monkeypatch.delenv("AF_COUNTER_NAME", raising=False)
    monkeypatch.setattr(ab.sys, "argv", ["-c"])
    assert ab.default_name() == "c"
    monkeypatch.setattr(ab.sys, "argv", [""])
    assert ab.default_name() == "unknown"


def test_an_explicit_name_still_wins(monkeypatch):
    monkeypatch.setenv("AF_COUNTER_NAME", "smoke test/../x")
    assert "/" not in ab.default_name()
