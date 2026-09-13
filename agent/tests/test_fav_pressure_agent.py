"""
Tests for the favourite-pressure agent.

The failure mode this file exists for is not inaccuracy, it is INVERSION: pick
the wrong side and the agent buys the underdog to lead at half time while
recording it as the favourite. Everything that decides the side — the 1X2 legs,
the name-to-side resolution, the halftime market lookup, the settlement — is
tested against the shapes PM actually publishes.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_fav_pressure_agent.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fav_pressure_agent as fa  # noqa: E402
import favourite_ht_table as fvt  # noqa: E402
import live_tracker as lt  # noqa: E402
from live_tracker import PressureSignals  # noqa: E402

HOME, AWAY = "CD Tolima", "Independiente del Valle"


def _mkt(question: str, yes: float, closed: bool = False) -> dict:
    return {
        "question": question,
        "closed": closed,
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps([f"yes-{question[:12]}", f"no-{question[:12]}"]),
        "outcomePrices": json.dumps([str(yes), str(round(1 - yes, 4))]),
        "conditionId": "cond",
    }


def _fixture(home_win=0.55, away_win=0.20, draw=0.25, kickoff=None) -> dict:
    """A board in PM's real shape: bare team names on the win and lead markets."""
    return {
        "title": f"{HOME} vs. {AWAY}",
        "kickoff": kickoff,
        "markets": [
            _mkt(f"Will {HOME} win on 2026-08-19?", home_win),
            _mkt(f"Will {AWAY} win on 2026-08-19?", away_win),
            _mkt(f"Will {HOME} vs. {AWAY} end in a draw?", draw),
            _mkt(f"{HOME} leading at halftime?", 0.30),
            _mkt(f"{AWAY} leading at halftime?", 0.14),
            _mkt(f"{HOME} vs. {AWAY}: Draw at halftime?", 0.52),
            # Neighbours that must never be mistaken for the 1X2 or the lead market
            _mkt(f"{HOME} to win the second half?", 0.40),
            _mkt(f"{HOME} vs. {AWAY}: Second half draw?", 0.35),
            _mkt(f"{HOME} vs. {AWAY}: 1st Half O/U 0.5", 0.60),
        ],
    }


# ── side resolution ──────────────────────────────────────────────────────────

def test_resolves_each_side_by_name():
    assert fa.resolve_side(HOME, HOME, AWAY) == "home"
    assert fa.resolve_side(AWAY, HOME, AWAY) == "away"


def test_resolves_across_spelling_differences():
    """PM writes the official name where api-football writes the short one."""
    assert fa.resolve_side("Coventry City FC", "Coventry", "Reims") == "home"


def test_a_name_that_fits_neither_side_fails_closed():
    assert fa.resolve_side("Liverpool", HOME, AWAY) is None


def test_a_name_that_fits_both_sides_fails_closed():
    """Two identically-scoring sides must produce no bet at all. Guessing here
    is how an agent ends up buying the opponent."""
    assert fa.resolve_side("Manchester", "Manchester United", "Manchester City") is None


# ── the favourite ────────────────────────────────────────────────────────────

def test_reads_the_favourite_and_devigs_all_three_legs():
    fav = fa.favourite_from_markets(_fixture(), HOME, AWAY)
    assert fav["side"] == "home" and fav["team"] == HOME
    assert fav["p_fav"] == pytest.approx(0.55 / 1.00)


def test_the_away_side_can_be_the_favourite():
    fav = fa.favourite_from_markets(_fixture(home_win=0.20, away_win=0.58, draw=0.22),
                                    HOME, AWAY)
    assert fav["side"] == "away" and fav["team"] == AWAY


def test_a_missing_leg_means_no_favourite():
    """Two-leg de-vig would inflate every probability by the missing leg's share
    and push fixtures into a stronger bucket than the market ever implied."""
    fx = _fixture()
    fx["markets"] = [m for m in fx["markets"]
                     if not m["question"].startswith("Will CD Tolima vs.")]
    assert fa.favourite_from_markets(fx, HOME, AWAY) is None


def test_second_half_markets_are_not_1x2_legs():
    fx = {"title": f"{HOME} vs. {AWAY}", "kickoff": None, "markets": [
        _mkt(f"{HOME} to win the second half?", 0.9),
        _mkt(f"{AWAY} to win the second half?", 0.05),
        _mkt(f"{HOME} vs. {AWAY}: Second half draw?", 0.05),
    ]}
    assert fa.favourite_from_markets(fx, HOME, AWAY) is None


# ── the halftime market ──────────────────────────────────────────────────────

def test_buys_the_favourites_lead_market_not_the_underdogs():
    fx = _fixture()
    got = fa.leading_at_ht_market(fx, HOME, AWAY, "home")
    assert got["question"] == f"{HOME} leading at halftime?"
    other = fa.leading_at_ht_market(fx, HOME, AWAY, "away")
    assert other["question"] == f"{AWAY} leading at halftime?"
    assert got["token_id"] != other["token_id"]


def test_the_halftime_draw_market_is_not_a_lead_market():
    fx = {"title": f"{HOME} vs. {AWAY}", "kickoff": None,
          "markets": [_mkt(f"{HOME} vs. {AWAY}: Draw at halftime?", 0.52)]}
    assert fa.leading_at_ht_market(fx, HOME, AWAY, "home") is None
    assert fa.leading_at_ht_market(fx, HOME, AWAY, "away") is None


# ── the fair-value table ─────────────────────────────────────────────────────

def test_table_is_monotone_in_the_clock_and_in_the_favourites_price():
    table = fvt.load()
    pooled = [fvt.lookup(table, m, True, None)[0] for m in range(15, 41)]
    assert pooled == sorted(pooled, reverse=True)
    weak = fvt.lookup(table, 15, True, 0.40)[0]
    strong = fvt.lookup(table, 15, True, 0.70)[0]
    assert strong > weak


def test_venue_is_never_pooled_away():
    """A home favourite and an away one are different populations, and the venue
    is always known — unlike the price, it never needs a fallback."""
    table = fvt.load()
    assert fvt.lookup(table, 20, True, 0.60)[0] != fvt.lookup(table, 20, False, 0.60)[0]


def test_off_grid_states_return_nothing_rather_than_a_guess():
    table = fvt.load()
    assert fvt.lookup(table, 60, True, None) == (None, 0)


def test_entry_window_is_inside_the_grid():
    table = fvt.load()
    for minute in (fa.ENTRY_MIN_MINUTE, fa.ENTRY_MAX_MINUTE):
        p, n = fvt.lookup(table, minute, True, 0.60)
        assert p is not None and n > 0


# ── entry gates ──────────────────────────────────────────────────────────────

def _sig(**kw) -> PressureSignals:
    base = dict(fixture_id=1, home=HOME, away=AWAY, minute=16, score="0-0",
                has_stats=True)
    base.update(kw)
    return PressureSignals(**base)


def _pressing_home(minute: int = 16, **kw) -> PressureSignals:
    """A home side clearly on top: top-decile own pressure, top-quartile gap."""
    return _sig(minute=minute, home_shots_on_total=3, home_shots_inside_total=4,
                home_xg_total=0.7, home_corners_total=3, home_possession=63.0,
                away_possession=37.0, **kw)


def _row(**kw) -> dict:
    # The opening_* fields are the frozen control from obs_version 3 on; the
    # *_now fields are what the gate reads.
    row = {"opening_fav_pressure": 40.0, "opening_dog_pressure": 5.0,
           "opening_dominance": 35.0,
           "fav_pressure_now": 40.0, "dog_pressure_now": 5.0,
           "dominance_now": 35.0, "pressure_source": "opening",
           "score_agrees": True, "ladder_goals": 0}
    row.update(kw)
    return row


def _book(ask=0.30, depth=200.0, bid=None) -> dict:
    # best_bid has been part of the gate since obs_version 2 added MAX_SPREAD;
    # the default sits one tick inside it so a test that is about something
    # else is not silently blocked by the spread.
    return {"best_ask": ask, "best_bid": ask - 0.01 if bid is None else bid,
            "ask_depth_usd": depth}


def _fav(p=0.60, prematch=True) -> dict:
    return {"side": "home", "team": HOME, "p_fav": p, "prematch": prematch}


def test_why_not_names_the_binding_gate():
    ok = _sig(minute=17)
    assert "0-0" in fa._why_not(_row(), _book(), _sig(home_goals=1), _fav())
    assert "minute" in fa._why_not(_row(), _book(), _sig(minute=12), _fav())
    assert "kickoff" in fa._why_not(_row(), _book(), ok, _fav(prematch=False))
    assert "favourite" in fa._why_not(_row(), _book(), ok, _fav(p=0.42))
    assert "window" in fa._why_not(_row(fav_pressure_now=None), _book(), ok, _fav())
    assert "pressing" in fa._why_not(_row(fav_pressure_now=5.0), _book(), ok, _fav())
    assert "on top" in fa._why_not(_row(dominance_now=2.0), _book(), ok, _fav())
    assert "ask" in fa._why_not(_row(), _book(ask=0.95), ok, _fav())
    assert "depth" in fa._why_not(_row(), _book(depth=1.0), ok, _fav())


def test_a_dominant_underdog_is_not_a_signal():
    """The thesis is the FAVOURITE living up to its price. The same match with
    the roles reversed must not enter."""
    row = _row(fav_pressure_now=5.0, dog_pressure_now=40.0, dominance_now=-35.0)
    assert fa._why_not(row, _book(), _sig(minute=17), _fav()) != ""


def test_thresholds_stay_reachable():
    """Both are set at measured percentiles of real openings — p90 per side and
    p75 for the gap. Anything much higher fires once a month."""
    assert fa.MIN_FAV_PRESSURE <= 30.0
    assert fa.MIN_DOMINANCE <= 20.0


# ── observe(), end to end ────────────────────────────────────────────────────

@pytest.fixture
def board(monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(fa, "_fetch_book", lambda tok: {
        **tok, "best_bid": 0.28, "best_ask": 0.30,
        "bid_depth_usd": 300.0, "ask_depth_usd": 300.0})
    # Kickoff in the future so the favourite counts as captured pre-match.
    fx = _fixture(kickoff=datetime.now(timezone.utc) + timedelta(minutes=5))
    fx["markets"] += [_mkt(f"{HOME} vs. {AWAY}: O/U 0.5", 0.40)]
    return [fx]


def test_a_dominant_favourite_enters(board):
    state = fa.FavState()
    rows = fa.observe({1: _pressing_home()}, fvt.load(), board, state)
    assert len(rows) == 1
    r = rows[0]
    assert r["would_enter"], r["skip_reason"]
    assert r["fav_side"] == "home" and r["fav_team"] == HOME
    assert r["token_id"].startswith("yes-CD Tolima")     # the favourite's market
    assert r["fair_base"] and r["edge_base_pp"] is not None


def test_the_underdog_pressing_does_not_enter(board):
    state = fa.FavState()
    away_pressing = _sig(minute=16, away_shots_on_total=3, away_shots_inside_total=4,
                         away_xg_total=0.7, away_corners_total=3,
                         home_possession=37.0, away_possession=63.0)
    rows = fa.observe({1: away_pressing}, fvt.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["opening_dominance"] < 0


def test_a_favourite_that_takes_over_late_can_still_enter(board):
    """The change obs_version 3 exists for: a favourite level and quiet at 15'
    that starts dominating at 30' was unenterable while the gate read a frozen
    opening. The frozen reading is still recorded, and still says no — that is
    the control the late arm has to be compared against."""
    state = fa.FavState()
    quiet = _sig(minute=16, home_possession=50.0, away_possession=50.0)
    fa.observe({1: quiet}, fvt.load(), board, state)

    surge = _sig(minute=30, has_window=True,
                 home_shots_on_window=3, home_shots_inside_window=4,
                 home_xg_window=0.7, home_corners_window=3,
                 home_possession=63.0, away_possession=37.0,
                 home_xg_total=0.7)
    rows = fa.observe({1: surge}, fvt.load(), board, state)
    r = rows[0]
    assert r["pressure_source"] == "window"
    assert r["dominance_now"] >= fa.MIN_DOMINANCE
    assert r["opening_dominance"] < fa.MIN_DOMINANCE      # the control disagrees
    assert r["would_enter"], r["skip_reason"]


def test_the_side_of_a_late_reading_is_still_the_favourite_s(board):
    """Inversion is this agent's failure mode and the window path has its own
    home/away split. An underdog surging at 30' must not be recorded as the
    favourite pressing."""
    state = fa.FavState()
    fa.observe({1: _sig(minute=16)}, fvt.load(), board, state)
    away_surge = _sig(minute=30, has_window=True,
                      away_shots_on_window=3, away_shots_inside_window=4,
                      away_xg_window=0.7, away_corners_window=3,
                      home_possession=37.0, away_possession=63.0,
                      away_xg_total=0.7)
    rows = fa.observe({1: away_surge}, fvt.load(), board, state)
    assert rows[0]["fav_side"] == "home"
    assert rows[0]["dominance_now"] < 0
    assert not rows[0]["would_enter"]


def test_the_1x2_is_captured_once_and_not_re_read(board):
    """The 1X2 drifts while the match stays goalless. Re-reading it would change
    both the favourite gate and the strength bucket mid-fixture."""
    state = fa.FavState()
    rows = fa.observe({1: _pressing_home()}, fvt.load(), board, state)
    captured = rows[0]["fav_prob"]

    # PM's board moves: the favourite is now priced much shorter.
    for m in board[0]["markets"]:
        if m["question"].startswith(f"Will {HOME} win"):
            m["outcomePrices"] = json.dumps(["0.80", "0.20"])
    rows = fa.observe({1: _pressing_home(20)}, fvt.load(), board, state)
    assert rows[0]["fav_prob"] == pytest.approx(captured)


def test_the_1x2_is_swept_before_kickoff_not_when_the_match_goes_live(board):
    """The capture has to happen across the WHOLE PM universe, not just the
    fixtures currently live — by minute 15 the 1X2 has been drifting for a
    quarter of an hour. Sweeping only live fixtures is how this agent would
    never have entered a single trade."""
    state = fa.FavState()
    assert fa.capture_prematch_odds(state, board) == 1
    assert state.odds[board[0]["title"]]["prematch"] is True
    # The sweep is idempotent: a second pass must not overwrite with a later price.
    assert fa.capture_prematch_odds(state, board) == 0


def test_a_fixture_first_seen_in_play_never_enters(monkeypatch):
    """After kickoff the 1X2 has already drifted with the goalless clock, so the
    favourite it names is not the quantity the table is bucketed on."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(fa, "_fetch_book", lambda tok: {
        **tok, "best_bid": 0.28, "best_ask": 0.30,
        "bid_depth_usd": 300.0, "ask_depth_usd": 300.0})
    fx = _fixture(kickoff=datetime.now(timezone.utc) - timedelta(minutes=16))
    rows = fa.observe({1: _pressing_home()}, fvt.load(), [fx], fa.FavState())
    assert not rows[0]["would_enter"]
    assert rows[0]["fav_is_prematch"] is False
    assert "kickoff" in rows[0]["skip_reason"]


def test_a_goal_ends_it(board):
    state = fa.FavState()
    fa.observe({1: _pressing_home()}, fvt.load(), board, state)
    rows = fa.observe({1: _pressing_home(20, home_goals=1)}, fvt.load(), board, state)
    assert not rows[0]["would_enter"]
    assert rows[0]["fair_base"] is None       # the table is conditional on 0-0


def test_no_stats_never_enters(board):
    rows = fa.observe({1: _sig(has_stats=False)}, fvt.load(), board, fa.FavState(),
                      enrich_status={1: lt.RATE_LIMITED})
    assert not rows[0]["would_enter"]
    assert "rate limited" in rows[0]["skip_reason"]
    assert rows[0]["opening_fav_pressure"] is None


# ── settlement: the call count is the bug ────────────────────────────────────
# On 2026-09-10 this settle spent 143,613 api-football calls against a 75,000/day
# key — one per pending fixture per run, on 5,560 fixtures that could never
# settle. These pin the three things that made it unbounded.


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class _NoCounter:
    def record(self, kind, n=1):
        pass


def _af_fixture(fid, ht=(1, 0), status="FT"):
    return {"fixture": {"id": fid, "status": {"short": status}},
            "score": {"halftime": {"home": ht[0], "away": ht[1]}}}


@pytest.fixture
def af(monkeypatch):
    """A fake api-football. Also keeps the tests off the real call counter,
    which the live daemons read to ration the day."""
    calls: list[list[int]] = []
    replies: dict = {"body": None}

    def fake_get(url, params=None, **kw):
        ids = [int(x) for x in params["ids"].split("-")]
        calls.append(ids)
        body = replies["body"] or {"errors": [], "response": [_af_fixture(i) for i in ids]}
        return _Resp(body)

    monkeypatch.setenv("FOOTBALL_API_KEY", "k")
    monkeypatch.setattr(fa.requests, "get", fake_get)
    monkeypatch.setattr(fa.af_budget, "process_counter", lambda: _NoCounter())
    return calls, replies


def test_halftime_scores_are_batched_twenty_ids_a_call(af):
    calls, _ = af
    out = fa._halftime_scores(list(range(1, 46)))
    assert [len(c) for c in calls] == [20, 20, 5]
    assert out[45] == (1, 0)


def test_a_refusal_stops_the_run_instead_of_spending_the_rest(af):
    calls, replies = af
    replies["body"] = {"errors": {"requests": "You have reached the request limit for the day"},
                       "response": []}
    assert fa._halftime_scores(list(range(1, 101))) == {}
    assert len(calls) == 1


def test_espn_ids_and_unfinished_halves_are_never_settled_from_the_api(af):
    calls, replies = af
    replies["body"] = {"errors": [], "response": [_af_fixture(7, status="1H"),
                                                  _af_fixture(8, status="HT")]}
    out = fa._halftime_scores([-401234, 7, 8])
    assert calls == [[7, 8]]            # the negative (ESPN) id is never sent
    assert out == {8: (1, 0)}           # a half still in play is not a result


class _Cur:
    def __init__(self, conn):
        self.conn, self.rowcount, self._rows = conn, 0, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self.conn.params.append(params)
        if sql.lstrip().startswith("UPDATE"):
            self.rowcount, self._rows = 0, []
        elif "SELECT id, fixture_id" in sql:
            self._rows = self.conn.pending
        else:
            self._rows = self.conn.tape

    def fetchall(self):
        return self._rows


class _Conn:
    autocommit = True                   # db_txn.atomic reads it

    def __init__(self, pending, tape):
        self.pending, self.tape, self.sql, self.params = pending, tape, [], []

    def cursor(self, cursor_factory=None):
        return _Cur(self)

    def commit(self):
        pass


def test_settle_never_asks_about_rows_without_a_favourite_or_stale_fixtures(monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=fa.SETTLE_API_MAX_AGE_H + 1)
    asked: list[list[int]] = []
    monkeypatch.setattr(fa, "_halftime_scores", lambda ids: asked.append(list(ids)) or {})
    conn = _Conn(
        pending=[{"id": 1, "fixture_id": 5, "minute": 20, "fav_side": "home", "paper_trade_id": None},
                 {"id": 2, "fixture_id": 6, "minute": 20, "fav_side": "away", "paper_trade_id": None}],
        tape=[(5, stale), (6, now - timedelta(hours=1))],     # (fixture, last seen)
    )
    assert fa.settle(conn) == 0         # the API answered neither
    assert asked == [[6]]               # 5 is past the horizon: no call
    closes = [s for s in conn.sql if s.lstrip().startswith("UPDATE")]
    assert closes and "fav_side IS NULL" in closes[0]
    assert any("'no_api'" in s for s in closes)    # 5 closed with no outcome
    pending_sql = next(s for s in conn.sql if "SELECT id, fixture_id" in s)
    assert "fav_side IS NOT NULL" in pending_sql


# ── settlement: never from our own tape (2026-09-13) ─────────────────────────
# pt#5977, Dunkerque v Saint-Etienne: the API did not answer, api-football held
# the minute at 45 through the break, and the tape's FIRST "45'" row was a 0-0
# flap a minute after Saint-Etienne's 43' goal. A 0-1 half-time lead for the
# favourite was booked as a loss.

def _recent():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(minutes=40)


def test_an_unanswered_fixture_waits_and_is_never_read_off_the_tape(monkeypatch):
    monkeypatch.setattr(fa, "_halftime_scores", lambda ids: {})
    conn = _Conn(
        pending=[{"id": 1, "fixture_id": 1552470, "minute": 15, "fav_side": "away",
                  "paper_trade_id": 5977}],
        tape=[(1552470, _recent())],
    )
    assert fa.settle(conn) == 0
    writes = [s for s in conn.sql
              if s.lstrip().startswith("UPDATE") and "fav_side IS NULL" not in s]
    assert writes == []


def test_a_traded_fixture_is_asked_past_the_horizon_and_never_closed_blind(monkeypatch):
    from datetime import datetime, timedelta, timezone
    stale = datetime.now(timezone.utc) - timedelta(hours=fa.SETTLE_API_MAX_AGE_H + 1)
    asked: list[list[int]] = []
    monkeypatch.setattr(fa, "_halftime_scores", lambda ids: asked.append(list(ids)) or {})
    conn = _Conn(
        pending=[{"id": 1, "fixture_id": 9, "minute": 20, "fav_side": "home", "paper_trade_id": 77}],
        tape=[(9, stale)],
    )
    assert fa.settle(conn) == 0
    assert asked == [[9]]
    assert not [s for s in conn.sql if "'no_api'" in s]


def test_the_outcome_is_the_half_time_score_read_from_the_favourites_side(monkeypatch):
    monkeypatch.setattr(fa, "_halftime_scores", lambda ids: {1552470: (0, 1)})
    conn = _Conn(
        pending=[{"id": 1, "fixture_id": 1552470, "minute": 15, "fav_side": "away",
                  "paper_trade_id": 5977}],
        tape=[(1552470, _recent())],
    )
    assert fa.settle(conn) == 1
    sent = list(zip(conn.sql, conn.params))
    assert [p for s, p in sent if "SET ht_home_goals" in s] == [(0, 1, True, "api", 1)]
    assert [p for s, p in sent if "UPDATE paper_trades" in s] == [("won", True, 5977)]
