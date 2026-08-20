"""
Tests for the stats-enrichment budget in LiveMatchTracker.

These exist because the old code collapsed every enrichment failure into
`return False`, so a spent api-football quota was indistinguishable from a
competition that genuinely has no stats. The agent then wrote "no api-football
stats coverage" on 36,917 rows in a single day when the real answer was that we
had stopped being allowed to ask. Each test below pins one half of that
distinction, or the budget that stops us hitting the wall in the first place.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_live_tracker_budget.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_tracker as lt  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _live_payload(n, minute=80, league=None):
    """n live fixtures, none carrying inline statistics."""
    return {"response": [
        {
            "fixture": {"id": 1000 + i, "status": {"elapsed": minute}},
            "teams": {"home": {"name": f"Home{i}"}, "away": {"name": f"Away{i}"}},
            "goals": {"home": 0, "away": 0},
            "league": {"name": league or f"League{i % 4}"},
            "events": [],
        }
        for i in range(n)
    ]}


def _stats_payload(shots=7):
    return {"response": [
        {"team": {"name": "Home0"}, "statistics": [{"type": "Total Shots", "value": shots}]},
        {"team": {"name": "Away0"}, "statistics": [{"type": "Total Shots", "value": 3}]},
    ]}


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("FOOTBALL_API_KEY", "test-key")
    t = lt.LiveMatchTracker()
    t.api_key = "test-key"
    return t


def _install(monkeypatch, live_n, stats_response, calls, league=None):
    def fake_get(url, **kw):
        if "statistics" in url:
            calls.append(kw["params"]["fixture"])
            return stats_response() if callable(stats_response) else stats_response
        return FakeResp(_live_payload(live_n, league=league))
    monkeypatch.setattr(lt.requests, "get", fake_get)


def test_budget_caps_calls_per_cycle(tracker, monkeypatch):
    """90 eligible fixtures must not cost 90 calls — that is the 130k/day burn."""
    calls = []
    _install(monkeypatch, 90, FakeResp(_stats_payload()), calls)
    tracker.poll()
    assert len(calls) == lt.ENRICH_BUDGET_PER_CYCLE
    assert tracker.last_enrich_report.get("over budget") == 90 - lt.ENRICH_BUDGET_PER_CYCLE


def test_priority_order_is_honoured(tracker, monkeypatch):
    """The caller's ranking decides who gets the scarce calls.

    The fixture count is derived from the budget rather than hard-coded: the
    property under test is "the top N by rank win", and it stops being tested at
    all the moment the budget is raised past the number of fixtures."""
    live_n = lt.ENRICH_BUDGET_PER_CYCLE + 5
    calls = []
    _install(monkeypatch, live_n, FakeResp(_stats_payload()), calls)
    # rank purely by fixture id so the expected winners are unambiguous
    tracker.poll(priority=lambda fid, snap: fid)
    assert calls == sorted(calls, reverse=True)
    assert len(calls) == lt.ENRICH_BUDGET_PER_CYCLE
    assert min(calls) == 1000 + live_n - lt.ENRICH_BUDGET_PER_CYCLE


def test_negative_priority_is_never_called(tracker, monkeypatch):
    """A fixture PM does not list can never be traded, so it costs nothing."""
    calls = []
    _install(monkeypatch, 20, FakeResp(_stats_payload()), calls)
    tracker.poll(priority=lambda fid, snap: -1)
    assert calls == []
    assert tracker.last_enrich_report.get("deprioritised") == 20


def test_ttl_prevents_refetch(tracker, monkeypatch):
    """Stats do not move fast enough to justify a call every 60s."""
    calls = []
    _install(monkeypatch, 5, FakeResp(_stats_payload()), calls)
    tracker.poll()
    first = len(calls)
    tracker.snapshots.clear()          # force fresh snapshots, same fixture ids
    tracker.poll()
    assert len(calls) == first, "second poll inside the TTL must not re-fetch"
    assert tracker.last_enrich_report.get("within TTL") == 5


def test_quota_error_is_not_reported_as_no_coverage(tracker, monkeypatch):
    """The bug this whole module exists for: 200 + errors != 'no stats'."""
    calls = []
    quota = FakeResp({"errors": {"requests": "You have reached the request "
                                             "limit for the day"}, "response": []})
    _install(monkeypatch, 10, quota, calls)
    tracker.poll()
    statuses = set(tracker.enrich_status.values())
    assert "quota" in statuses
    assert "empty" not in statuses
    # and it must stop asking once told the day is spent
    assert len(calls) == 1


def test_empty_response_is_real_no_coverage(tracker, monkeypatch):
    """A clean 200 with no rows is the one case that IS a coverage gap."""
    calls = []
    _install(monkeypatch, 3, FakeResp({"response": []}), calls)
    tracker.poll()
    assert set(tracker.enrich_status.values()) == {"empty"}
    assert len(calls) == 3


def test_uncovered_league_stops_being_retried(tracker, monkeypatch):
    """Coverage is a property of the competition, so learn it once."""
    calls = []
    # all four fixtures share one league, already struck out three times
    _install(monkeypatch, 4, FakeResp({"response": []}), calls, league="Dead League")
    tracker._empty_leagues["Dead League"] = {1, 2, 3}
    tracker.poll()
    assert calls == [], "a league proven uncovered must not be paid for again"
    assert tracker.last_enrich_report.get("league uncovered") == 4
