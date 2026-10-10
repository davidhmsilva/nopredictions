"""The venue open recorder: an open is a two-sided quote no wider than 10¢,
empty sides are None rather than 0/1, only the four traded families and the
main + More Markets events are read, Kalshi football never records a quote that
could be in play, and the upsert never moves an open once set."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import venue_open_recorder as vor  # noqa: E402

NOW = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)


def _market(kind, question, outcomes, tokens, bid, ask, line=None, item=None):
    return {"sportsMarketType": kind, "question": question, "outcomes": str(outcomes).replace("'", '"'),
            "clobTokenIds": str(tokens).replace("'", '"'), "conditionId": "0xc", "bestBid": bid,
            "bestAsk": ask, "line": line, "groupItemTitle": item, "liquidityNum": 900.0,
            "acceptingOrdersTimestamp": "2026-09-27T10:00:00Z"}


def _event(title, markets, start="2026-10-11T15:00:00Z"):
    return {"title": title, "slug": "x", "startTime": start, "markets": markets,
            "tags": [{"slug": "soccer", "label": "Soccer"}, {"slug": "epl", "label": "Premier League"}]}


def test_open_needs_both_sides_and_a_real_spread():
    assert vor.is_open_quote(0.45, 0.47)
    assert vor.is_open_quote(0.40, 0.50)                 # exactly 10¢ is a price
    assert not vor.is_open_quote(0.03, 0.98)             # Kalshi's placeholder book
    assert not vor.is_open_quote(None, 0.60)             # ask-only ladder
    assert not vor.is_open_quote(0.55, 0.50)             # crossed


def test_empty_sides_are_none_not_zero_or_one():
    assert vor.clean_quote(0, 1) == (None, None)
    assert vor.clean_quote("0.41", "0.43") == (0.41, 0.43)


def test_pm_reads_main_and_more_markets_only_in_traded_families():
    main = _event("Arsenal FC vs. Leeds United FC", [
        _market("moneyline", "Will Arsenal FC win on 2026-10-11?", ["Yes", "No"], ["t1", "t1n"],
                0.67, 0.68, item="Arsenal FC"),
        _market("moneyline", "Will Arsenal FC vs. Leeds United FC end in a draw?", ["Yes", "No"],
                ["t2", "t2n"], 0.21, 0.22, item="Draw (Arsenal FC vs. Leeds United FC)"),
    ])
    more = _event("Arsenal FC vs. Leeds United FC - More Markets", [
        _market("totals", "Arsenal FC vs. Leeds United FC: O/U 2.5", ["Over", "Under"], ["t3", "t3n"],
                0.52, 0.53, line=2.5),
        _market("total_corners", "Arsenal FC vs. Leeds United FC: O/U 9.5 Corners", ["Over", "Under"],
                ["t4", "t4n"], 0.5, 0.52, line=9.5),
    ])
    exact = _event("Arsenal FC vs. Leeds United FC - Exact Score", [
        _market("moneyline", "Exact score 1-0?", ["Yes", "No"], ["t5", "t5n"], 0.1, 0.11)])
    rows = vor.pm_soccer_rows([main, more, exact], NOW)
    by = {r["market_key"]: r for r in rows}
    assert set(by) == {"t1", "t2", "t3"}                 # no corners, no exact-score sibling
    assert by["t2"]["outcome"] == "Draw"
    assert by["t1"]["outcome"] == "Arsenal FC"
    assert by["t3"]["family"] == "totals" and by["t3"]["line"] == 2.5 and by["t3"]["outcome"] == "Over"
    assert by["t1"]["competition"] == "Premier League"
    assert by["t1"]["listed_at"] == datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)


def test_pm_skips_started_fixtures():
    ev = _event("A FC vs. B FC", [_market("moneyline", "Will A FC win?", ["Yes", "No"], ["t", "n"],
                                          0.5, 0.51, item="A FC")], start="2026-10-10T10:00:00Z")
    assert vor.pm_soccer_rows([ev], NOW) == []


def test_kalshi_football_stops_before_any_possible_kickoff():
    def fx(settles):
        return {"eventTicker": "KXEPLGAME-X", "competition": "EPL", "home": "A", "away": "B",
                "settlesAt": settles,
                "legs": {"home": {"ticker": "KX-A", "label": "A", "quote": {"bid": 0.4, "ask": 0.42,
                                                                             "askDepthUsd": 50}},
                         "draw": {"ticker": "KX-TIE", "label": "Tie", "quote": {"bid": 0.2, "ask": 0.22}}},
                "totals": {"2.5": {"ticker": "KX-T3", "quote": {"bid": 0.5, "ask": 0.53}}}}
    # settles in 5h: kick-off could be as early as settles − 4.5h = 0.5h from now — still recorded
    rows = vor.kalshi_soccer_rows({"fixtures": [fx((NOW + timedelta(hours=5)).isoformat())]}, NOW)
    assert {r["market_key"] for r in rows} == {"KX-A", "KX-TIE", "KX-T3"}
    assert rows[0]["kickoff"] == NOW + timedelta(hours=2)
    assert rows[0]["kickoff_source"] == "kalshi_settle_est"
    assert next(r for r in rows if r["market_key"] == "KX-TIE")["outcome"] == "Draw"
    # settles in 4h: the match may have started 30 min ago — nothing recorded
    assert vor.kalshi_soccer_rows({"fixtures": [fx((NOW + timedelta(hours=4)).isoformat())]}, NOW) == []


def test_sport_board_keys_pm_by_token_and_kalshi_by_game():
    board = {"games": [{
        "id": "401", "start": "2026-10-11T17:00:00Z", "state": "pre",
        "home": {"name": "Jacksonville Jaguars"}, "away": {"name": "Philadelphia Eagles"},
        "pmTokens": {"home": "tokH", "away": "tokA"},
        "total": {"line": 42.5, "over": {"bid": 0.47, "ask": 0.48}, "under": {"bid": 0.52, "ask": 0.53}},
        "venues": [{"venue": "polymarket", "source": "clob",
                    "quotes": {"home": {"bid": 0.76, "ask": 0.77, "askDepthUsd": 100},
                               "away": {"bid": 0.23, "ask": 0.24}}},
                   {"venue": "kalshi", "source": "kalshi",
                    "quotes": {"home": {"bid": 0.76, "ask": 0.77}, "away": {"bid": 0.23, "ask": 0.24}}}],
    }, {"id": "402", "start": "2026-10-10T10:00:00Z", "state": "in", "venues": []}]}
    rows = vor.sport_board_rows("nfl", board, NOW)
    keys = {(r["venue"], r["market_key"]) for r in rows}
    assert ("polymarket", "tokH") in keys and ("kalshi", "nfl:401:ml:home") in keys
    assert ("polymarket", "nfl:401:total:42.5:over") in keys
    assert len(rows) == 6 and all(r["event_key"] == "401" for r in rows)


def test_record_sets_open_only_on_an_open_grade_quote():
    base = dict(venue="kalshi", market_key="k", sport="soccer", competition=None, event_key="e",
                event_title="A vs B", home="A", away="B", family="moneyline", line=None,
                outcome="A", kickoff=NOW + timedelta(days=1), kickoff_source="x", listed_at=None,
                depth_usd=10.0, depth_kind="ask_usd", source="kalshi")
    rec = dict(zip(vor.COLS, vor.to_record({**base, "bid": 0.03, "ask": 0.98}, NOW)))
    assert rec["open_at"] is None and rec["first_bid"] == 0.03 and rec["last_ask"] == 0.98
    rec = dict(zip(vor.COLS, vor.to_record({**base, "bid": 0.40, "ask": 0.42}, NOW)))
    assert rec["open_at"] == NOW and rec["open_bid"] == 0.40 and rec["open_depth_usd"] == 10.0


def test_upsert_never_moves_an_open_once_set():
    sql = " ".join(vor.UPSERT.split())
    assert "open_at = COALESCE(t.open_at, EXCLUDED.open_at)" in sql
    for c in ("open_bid", "open_ask", "open_depth_usd", "open_source"):
        assert f"{c} = CASE WHEN t.open_at IS NULL THEN EXCLUDED.{c} ELSE t.{c} END" in sql
    # an unchanged price writes nothing
    assert "WHERE (t.open_at IS NULL AND EXCLUDED.open_at IS NOT NULL)" in sql


def test_dedupe_keeps_one_row_per_key():
    rows = [{"venue": "polymarket", "market_key": "a", "bid": 0.1},
            {"venue": "polymarket", "market_key": "a", "bid": 0.2},
            {"venue": "kalshi", "market_key": "a", "bid": 0.3}]
    out = vor.dedupe(rows)
    assert len(out) == 2 and {r["bid"] for r in out} == {0.2, 0.3}
