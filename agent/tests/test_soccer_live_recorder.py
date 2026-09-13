"""The soccer in-play recorder: siblings group into one game, books compress
without losing the top of book, a day's files read back whole, and a market is
only labelled with an outcome once it has actually resolved."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import soccer_live_recorder as slr  # noqa: E402

NOW = datetime(2026, 9, 13, 18, 30, tzinfo=timezone.utc)


def test_siblings_group_into_one_game():
    assert slr.group_of("PSV vs. Sparta Rotterdam - Exact Score") == "PSV vs. Sparta Rotterdam"
    assert slr.group_of("PSV vs. Sparta Rotterdam") == "PSV vs. Sparta Rotterdam"
    assert slr.group_of("Man City vs. Coventry - More Markets") == "Man City vs. Coventry"


def test_outrights_are_not_games():
    assert slr.is_game_event({"title": "PSV vs. Sparta Rotterdam - Halftime Result",
                              "startTime": "2026-09-13T18:00:00Z"})
    assert not slr.is_game_event({"title": "Premier League Winner 2026-27",
                                  "startTime": "2026-09-13T18:00:00Z"})
    assert not slr.is_game_event({"title": "PSV vs. Sparta Rotterdam"})


def test_window_runs_from_before_kickoff_to_after_the_whistle():
    ko = NOW - timedelta(minutes=100)
    assert slr.in_window(ko, NOW)
    assert slr.in_window(NOW + timedelta(minutes=9), NOW)
    assert not slr.in_window(NOW + timedelta(minutes=20), NOW)
    assert not slr.in_window(NOW - timedelta(minutes=slr.POST_MIN + 1), NOW)


def test_compact_book_keeps_top_of_book_and_depth():
    b = {"bids": [{"price": "0.40", "size": "100"}, {"price": "0.42", "size": "50"},
                  {"price": "0.30", "size": "10"}, {"price": "0.10", "size": "999"}],
         "asks": [{"price": "0.45", "size": "20"}, {"price": "0.44", "size": "30"}]}
    bid, bsz, ask, asz, busd, ausd = slr.compact_book(b)
    assert (bid, bsz, ask, asz) == (0.42, 50, 0.44, 30)
    assert busd == pytest.approx(0.42 * 50 + 0.40 * 100 + 0.30 * 10)
    assert ausd == pytest.approx(0.44 * 30 + 0.45 * 20)
    assert slr.compact_book({"bids": [], "asks": []}) == [None, 0, None, 0, 0, 0]


def test_outcome_only_once_resolved():
    assert slr.outcome_of({"closed": True, "outcomePrices": '["1", "0"]'}) == {"prices": [1.0, 0.0], "winner": 0}
    assert slr.outcome_of({"closed": True, "outcomePrices": '["0", "1"]'})["winner"] == 1
    assert slr.outcome_of({"closed": True, "outcomePrices": '["0.5", "0.5"]'})["winner"] is None
    assert slr.outcome_of({"closed": True, "outcomePrices": '["0.93", "0.07"]'}) is None   # closed, not final
    assert slr.outcome_of({"closed": False, "outcomePrices": '["1", "0"]'}) is None


def _event(title, closed=False):
    return {"slug": title.lower().replace(" ", "-"), "title": title, "startTime": "2026-09-13T17:00:00Z",
            "live": True, "score": "1-0", "period": "2H", "elapsed": "70", "ended": False,
            "markets": [{"conditionId": f"c-{title}", "clobTokenIds": json.dumps([f"{title}|0", f"{title}|1"]),
                         "outcomes": '["Yes", "No"]', "question": title, "sportsMarketType": "moneyline",
                         "lastTradePrice": 0.6, "closed": closed}]}


def test_run_once_writes_a_self_contained_day(tmp_path, monkeypatch):
    monkeypatch.setattr(slr, "OUT_DIR", str(tmp_path))
    evs = [_event("A vs. B"), _event("A vs. B - Exact Score", closed=True)]
    monkeypatch.setattr(slr, "fetch_window_events", lambda now: evs)
    monkeypatch.setattr(slr, "fetch_books", lambda toks: {t: [0.55, 10, 0.57, 12, 5.5, 6.8] for t in toks})
    r1 = slr.run_once(NOW)
    r2 = slr.run_once(NOW + timedelta(minutes=1))
    assert r1["games"] == 1 and r1["markets"] == 2 and r1["new_meta"] == 2 and r2["new_meta"] == 0
    snaps = list(slr.iter_snapshots("2026-09-13"))
    assert len(snaps) == 2
    q = snaps[0]["q"]
    assert q["c-A vs. B"] == [0.55, 10, 0.57, 12, 5.5, 6.8, 0.6, 0]
    assert q["c-A vs. B - Exact Score"][:1] == [None] and q["c-A vs. B - Exact Score"][-1] == 1
    assert snaps[0]["games"][0]["slug"] == "a-vs.-b"            # the main event names the game
    meta = slr.load_meta("2026-09-13")
    assert set(meta) == {"c-A vs. B", "c-A vs. B - Exact Score"} and meta["c-A vs. B"]["group"] == "A vs. B"
    assert sorted(m["i"] for m in meta.values()) == [0, 1]
    import gzip
    with gzip.open(os.path.join(str(tmp_path), "2026-09-13.jsonl.gz"), "rt") as fh:
        raw = json.loads(fh.readline())
    assert isinstance(raw["q"], list) and all(isinstance(r[0], int) for r in raw["q"])   # compact rows on disk
