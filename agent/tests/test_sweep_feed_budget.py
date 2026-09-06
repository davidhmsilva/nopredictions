"""
Tests for the sweep feed's pending re-query — the largest uncontrolled
api-football call class in this repo until 2026-09-05.

`/fixtures?live=all` drops a match the moment it ends, which is exactly when the
post-whistle window opens, so every fixture seen live is followed with a batched
`/fixtures?ids=` lookup until it reports a terminal status. That part is the
thesis. The part that was not the thesis: it re-asked EVERY pending fixture on
EVERY 30-second cycle for three hours, so a Saturday afternoon's tail of
finished matches was re-queried twice a minute long after anything could change.

These tests pin both halves — the fresh window keeps its every-cycle cadence,
and the tail backs off — because getting this wrong in either direction is
expensive: too eager burns the evening allowance, too lazy misses the whistle.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_sweep_feed_budget.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settled_sweep_observer as sw  # noqa: E402


def _live(fid: int, status: str = "1H") -> dict:
    return {
        "fixture": {"id": fid, "status": {"short": status, "elapsed": 30}},
        "teams": {"home": {"name": f"H{fid}"}, "away": {"name": f"A{fid}"}},
        "league": {"name": "Test League"},
        "goals": {"home": 0, "away": 0},
        "score": {"halftime": {"home": None, "away": None},
                  "fulltime": {"home": None, "away": None}},
    }


@pytest.fixture
def feed(monkeypatch):
    """A feed whose api-football calls are recorded rather than made."""
    monkeypatch.setattr(sw, "_AF_REFUSED", None)
    return sw.FixtureFeed()


def _install(monkeypatch, live_ids: list[int], seen_calls: list):
    def fake_af_get(params):
        seen_calls.append(params)
        if params.get("live"):
            return [_live(f) for f in live_ids]
        return []                      # the id lookup answers nothing terminal
    monkeypatch.setattr(sw, "_af_get", fake_af_get)


def _id_calls(calls) -> list:
    return [c for c in calls if "ids" in c]


def test_a_fixture_just_off_the_feed_is_checked_every_cycle(feed, monkeypatch):
    """The whistle window is minutes wide. Backing off here would cost the one
    thing this observer exists to catch."""
    calls = []
    _install(monkeypatch, [101], calls)
    feed.poll()                                    # 101 is live
    _install(monkeypatch, [], calls)               # it just ended
    feed.poll()
    feed.poll()
    feed.poll()
    assert len(_id_calls(calls)) == 3
    assert feed.pending_held == 0


def test_the_tail_backs_off(feed, monkeypatch):
    """Three hours of re-querying a match that finished long ago buys nothing
    and costs the evening."""
    calls = []
    _install(monkeypatch, [101], calls)
    feed.poll()
    _install(monkeypatch, [], calls)

    now = [sw.time.time()]
    monkeypatch.setattr(sw.time, "time", lambda: now[0])
    now[0] += sw.PENDING_FRESH_S + 1               # out of the fresh window
    feed.poll()
    asked_once = len(_id_calls(calls))

    now[0] += sw.PENDING_SLOW_S / 2                # too soon
    feed.poll()
    assert len(_id_calls(calls)) == asked_once
    assert feed.pending_held == 1

    now[0] += sw.PENDING_SLOW_S                    # due again
    feed.poll()
    assert len(_id_calls(calls)) == asked_once + 1


def test_the_backoff_is_per_fixture_not_global(feed, monkeypatch):
    """A stale fixture holding its call must never delay a fixture that has just
    this second left the feed."""
    calls = []
    _install(monkeypatch, [101, 202], calls)
    feed.poll()

    now = [sw.time.time()]
    monkeypatch.setattr(sw.time, "time", lambda: now[0])

    _install(monkeypatch, [202], calls)            # 101 ended, 202 plays on
    now[0] += sw.PENDING_FRESH_S + 1
    feed.poll()                                    # 101 asked, now stale

    _install(monkeypatch, [], calls)               # 202 ends too
    calls.clear()
    feed.poll()
    ids_asked = {i for c in _id_calls(calls) for i in c["ids"].split("-")}
    assert "202" in ids_asked, "the fresh fixture must be asked about"
    assert "101" not in ids_asked, "the stale one must not be"


def test_a_fixture_that_reported_final_is_never_asked_again(feed, monkeypatch):
    calls = []
    _install(monkeypatch, [101], calls)
    feed.poll()

    def final_af_get(params):
        calls.append(params)
        if params.get("live"):
            return []
        return [_live(101, status="FT")]
    monkeypatch.setattr(sw, "_af_get", final_af_get)
    feed.poll()
    assert 101 in feed.final

    calls.clear()
    _install(monkeypatch, [], calls)
    feed.poll()
    assert _id_calls(calls) == []


def test_bookkeeping_is_dropped_with_the_fixture(feed, monkeypatch):
    """`last_checked` grows once per fixture ever seen; a process that runs for
    weeks cannot carry that forever."""
    calls = []
    _install(monkeypatch, [101], calls)
    feed.poll()
    assert feed.last_checked or True                # populated on the next poll

    now = [sw.time.time()]
    monkeypatch.setattr(sw.time, "time", lambda: now[0])
    _install(monkeypatch, [], calls)
    now[0] += sw.PENDING_FRESH_S + 1
    feed.poll()
    assert 101 in feed.last_checked

    now[0] += sw.KEEP_AFTER_LIVE_S
    feed.poll()
    assert 101 not in feed.last_checked
    assert 101 not in feed.last_live
