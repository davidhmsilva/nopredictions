"""ESPN stats for competitions api-football will never cover (2026-09-13).

On 2026-09-12, 49 Polymarket-listed fixtures reached the first-half arms with no
reading at all, because api-football confirms it publishes no statistics for
their competitions. ESPN carries shots, corners and possession for 33 of them.
api-football keeps the fixture — id, minute, score — and ESPN fills only the
stat block. What can go wrong silently is pinned here: the wrong fixture's
stats, a reading labelled with the wrong feed, and a request per fixture where
one per league does.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_espn_uncovered_leagues.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import espn_stats  # noqa: E402
import live_tracker as lt  # noqa: E402

NATIONAL_LEAGUE = 43      # api-football: statistics_fixtures = false
URUGUAY = 268             # uncovered too, and NOT mapped — ESPN has no stats for it


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


def _live(fixtures, league_id=NATIONAL_LEAGUE):
    return {"response": [
        {
            "fixture": {"id": fid, "status": {"elapsed": minute}},
            "teams": {"home": {"name": home}, "away": {"name": away}},
            "goals": {"home": gh, "away": ga},
            "league": {"name": "National League", "id": league_id},
            "events": [],
        }
        for fid, home, away, minute, gh, ga in fixtures
    ]}


def _espn(home, away, minute=20, goals=(0, 0), sot=(3, 1), shots=(7, 2),
          corners=(4, 0), poss=(61.0, 39.0)):
    return espn_stats.EspnFixture(
        event_id="401", league_code="eng.5", league="National League",
        home=home, away=away, minute=minute, state="in", detail=f"{minute}'",
        home_stats=espn_stats.EspnTeamStats(
            shots_on=sot[0], shots_total=shots[0], corners=corners[0],
            possession=poss[0], goals=goals[0]),
        away_stats=espn_stats.EspnTeamStats(
            shots_on=sot[1], shots_total=shots[1], corners=corners[1],
            possession=poss[1], goals=goals[1]),
    )


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("FOOTBALL_API_KEY", "test-key")
    monkeypatch.setattr(lt.espn_stats, "live_fixtures", lambda *a, **k: [])
    t = lt.LiveMatchTracker()
    t.api_key = "test-key"
    # What the API answers for both leagues; seeded so no /leagues call is made.
    t._league_stats_coverage[NATIONAL_LEAGUE] = False
    t._league_stats_coverage[URUGUAY] = False
    return t


@pytest.fixture
def feeds(monkeypatch):
    """Installs a live payload and an ESPN board; records every call to each."""
    state = {"live": [], "league_id": NATIONAL_LEAGUE, "board": [],
             "stats_calls": [], "espn_calls": []}

    def fake_get(url, **kw):
        if "statistics" in url:
            state["stats_calls"].append(kw["params"]["fixture"])
            return FakeResp({"response": []})
        return FakeResp(_live(state["live"], state["league_id"]))

    def fake_fetch(code, session=None):
        state["espn_calls"].append(code)
        return list(state["board"])

    monkeypatch.setattr(lt.requests, "get", fake_get)
    monkeypatch.setattr(lt.espn_stats, "fetch_league", fake_fetch)
    return state


def test_an_uncovered_league_is_read_from_espn(tracker, feeds):
    feeds["live"] = [(1585212, "Boston United", "Wealdstone", 20, 0, 0)]
    feeds["board"] = [_espn("Boston United", "Wealdstone")]

    sig = tracker.poll()[1585212]

    assert sig.has_stats
    assert sig.stats_source == "espn"
    assert sig.has_inside is False
    assert sig.home_shots_on_total == 3 and sig.away_shots_total == 2
    assert sig.fixture_id > 0                      # still api-football's fixture
    assert feeds["stats_calls"] == []              # no paid call for a league it cannot answer
    assert feeds["espn_calls"] == ["eng.5"]
    assert tracker.enrich_status[1585212] == "ok (espn)"


def test_one_espn_request_per_league_not_per_fixture(tracker, feeds):
    feeds["live"] = [
        (1585212, "Boston United", "Wealdstone", 20, 0, 0),
        (1585218, "Woking", "Altrincham", 22, 0, 0),
        (1585210, "Harrogate Town", "Tamworth", 25, 0, 0),
    ]
    feeds["board"] = [_espn("Boston United", "Wealdstone"),
                      _espn("Woking", "Altrincham", minute=22),
                      _espn("Harrogate Town", "Tamworth", minute=25)]

    signals = tracker.poll()

    assert feeds["espn_calls"] == ["eng.5"]
    assert all(signals[f].stats_source == "espn" for f in (1585212, 1585218, 1585210))


def test_a_score_disagreement_fails_closed(tracker, feeds):
    """Two feeds, one match. If they disagree on the score, one lags a goal or
    the names paired the wrong fixture. Either way the reading is not taken:
    a lag costs one poll, and a wrong pairing would put another match's
    pressure on this one."""
    feeds["live"] = [(1585212, "Boston United", "Wealdstone", 20, 0, 0)]
    feeds["board"] = [_espn("Boston United", "Wealdstone", goals=(1, 0))]

    sig = tracker.poll()[1585212]

    assert not sig.has_stats
    assert tracker.enrich_status[1585212].endswith("espn: score disagrees")


def test_an_unmatched_fixture_gets_no_reading(tracker, feeds):
    feeds["live"] = [(1585212, "Boston United", "Wealdstone", 20, 0, 0)]
    feeds["board"] = [_espn("Woking", "Altrincham")]

    sig = tracker.poll()[1585212]

    assert not sig.has_stats
    assert tracker.enrich_status[1585212].endswith("espn: no fixture matched")


def test_an_unmapped_uncovered_league_keeps_its_label(tracker, feeds):
    """Uruguay is uncovered on api-football AND stat-less on ESPN. It must keep
    the old label and cost ESPN nothing."""
    feeds["league_id"] = URUGUAY
    feeds["live"] = [(1637483, "Racing Montevideo", "Boston River", 20, 0, 0)]

    sig = tracker.poll()[1637483]

    assert not sig.has_stats
    assert feeds["espn_calls"] == []
    assert tracker.enrich_status[1637483] == "league has no stats coverage (api)"


def test_a_carried_espn_block_keeps_its_label(tracker, feeds):
    """A poll where ESPN misses carries the last block forward. It must stay
    labelled ESPN: relabelled api-football with has_inside=True, danger_index
    would score ESPN's missing shots-in-box as a measured zero."""
    feeds["live"] = [(1585212, "Boston United", "Wealdstone", 20, 0, 0)]
    feeds["board"] = [_espn("Boston United", "Wealdstone")]
    tracker.poll()

    feeds["live"] = [(1585212, "Boston United", "Wealdstone", 21, 0, 0)]
    feeds["board"] = []
    sig = tracker.poll()[1585212]

    assert sig.has_stats                           # carried, not lost
    assert sig.stats_source == "espn"
    assert sig.has_inside is False
