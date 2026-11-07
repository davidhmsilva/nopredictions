"""close_model + close_maker_agent: the pure parts — de-vig, no lookahead in the
team walk, the bid price, the side choice and the fill rule."""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import close_maker_agent as ag  # noqa: E402
import close_model as cm        # noqa: E402


def test_devig_rows_sum_to_one_and_keep_order():
    p = cm.devig(np.array([[2.0, 3.4, 4.0], [1.3, 5.5, 11.0]]))
    assert np.allclose(p.sum(1), 1.0)
    assert p[0, 0] > p[0, 2] > 0 and p[1, 0] > 0.7


def _matches(rows):
    m = pd.DataFrame(rows, columns=["match_id", "league", "season", "kickoff_utc", "h", "a", "hs", "as_",
                                    "hst", "ast", "hsh", "ash"])
    m["kickoff_utc"] = pd.to_datetime(m.kickoff_utc, utc=True)
    for c in ("cl_oh", "cl_od", "cl_oa", "pre_oh", "pre_od", "pre_oa", "cl_bk"):
        m[c] = np.nan
    return m


def test_walk_never_lets_a_match_see_itself():
    m = _matches([(1, "ENG-PR", 2025, "2025-08-10", 10, 20, 3, 0, 8, 1, 15, 5),
                  (2, "ENG-PR", 2025, "2025-08-17", 20, 10, 0, 0, 2, 2, 9, 9)])
    f, st = cm.walk(m)
    # the first match is read from empty states
    assert f.loc[0, "h_gd_f"] == 0 and f.loc[0, "a_gd_f"] == 0 and f.loc[0, "h_n"] == 0
    # the second sees only the first: team 10 (now away) won 3-0
    assert f.loc[1, "a_gd_f"] > 0 and f.loc[1, "h_gd_f"] < 0 and f.loc[1, "a_n"] == 1
    assert f.loc[1, "a_rest"] == 7.0 or f.loc[1, "a_rest"] == 7
    assert st[10]["n"] == 2


def test_missing_shots_do_not_count_as_zero():
    m = _matches([(1, "ENG-PR", 2025, "2025-08-10", 10, 20, 1, 0, np.nan, np.nan, np.nan, np.nan)])
    _, st = cm.walk(m)
    assert st[10]["sot_f"] == 0.0 and st[10]["gd_f"] > 0     # untouched, not pulled toward a fake 0-0


def test_predict_is_the_price_when_the_model_is_neutral():
    spec = {"features": ["l0"] + cm.FEATURES, "coef": [1.0] + [0.0] * len(cm.FEATURES), "intercept": 0.0}
    x = {k: 0.0 for k in cm.FEATURES}
    assert abs(cm.predict(spec, float(cm.logit(0.4)), x) - 0.4) < 1e-9


def test_bid_improves_by_a_tick_only_when_there_is_room():
    assert ag.bid_price({"bid": 0.40, "ask": 0.43}, q=0.45) == 0.41
    assert ag.bid_price({"bid": 0.40, "ask": 0.41}, q=0.45) == 0.40
    assert ag.bid_price({"bid": 0.40, "ask": 0.43}, q=0.425) is None     # within 2pp of the model


def _bk(bid, ask, depth=500.0):
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
            "bid_depth": depth, "ask_depth": depth}


def test_normalised_refuses_a_broken_book():
    books = {"home": _bk(0.40, 0.42), "draw": _bk(0.27, 0.28), "away": _bk(0.31, 0.32)}
    p = ag.normalised(books)
    assert p is not None and abs(sum(p.values()) - 1) < 1e-9
    assert ag.normalised({**books, "draw": None}) is None
    assert ag.normalised({**books, "draw": _bk(0.10, 0.90)}) is None                  # placeholder ladder
    assert ag.normalised({**books, "home": _bk(0.60, 0.62)}) is None                  # sums to 1.2


def test_choose_takes_the_bigger_gap_and_respects_the_book():
    books = {"home": _bk(0.40, 0.42), "draw": _bk(0.27, 0.28), "away": _bk(0.31, 0.32)}
    p = ag.normalised(books)
    side, price, _ = ag.choose({"home": p["home"] + 0.03, "away": p["away"] + 0.025}, p, books)
    assert side == "home" and price == 0.41          # two ticks wide: improve by one
    assert ag.choose({"home": p["home"] + 0.01, "away": p["away"]}, p, books) is None
    thin = {**books, "home": _bk(0.40, 0.42, depth=10.0)}
    side, _, _ = ag.choose({"home": p["home"] + 0.05, "away": p["away"] + 0.03}, p, thin)
    assert side == "away"


def test_fill_needs_a_print_below_the_bid_or_the_ask_at_it():
    yes, no = "Y", "N"
    t0 = 1_000.0
    at_bid = [{"token_id": yes, "price": 0.40, "timestamp": t0 + 10}]
    below = [{"token_id": yes, "price": 0.39, "timestamp": t0 + 20}]
    no_side = [{"token_id": no, "price": 0.62, "timestamp": t0 + 30}]                 # Yes-equivalent 0.38
    before = [{"token_id": yes, "price": 0.30, "timestamp": t0 - 5}]                  # before we posted
    book_above = _bk(0.39, 0.41)
    assert ag.fill_from(book_above, at_bid, yes, 0.40, t0)[0] is None
    assert ag.fill_from(book_above, at_bid, yes, 0.40, t0)[2] == t0 + 10           # touched only
    assert ag.fill_from(book_above, below, yes, 0.40, t0)[:2] == ("traded_through", t0 + 20)
    assert ag.fill_from(book_above, no_side, yes, 0.40, t0)[0] == "traded_through"
    assert ag.fill_from(book_above, before, yes, 0.40, t0)[0] is None
    assert ag.fill_from(_bk(0.38, 0.40), [], yes, 0.40, t0)[0] == "ask_at_bid"


def test_history_gap_judges_a_team_against_its_own_league():
    ko = datetime(2026, 10, 10, 16, 30, tzinfo=timezone.utc)
    last = (ko - timedelta(days=20)).isoformat()            # a three-week break: fine
    states = {"teams": {"1": {"league": "ENG-PR", "last": last}, "2": {"league": "ENG-PR", "last": last},
                        "3": {"league": "ENG-PR", "last": (ko - timedelta(days=45)).isoformat()}}}
    f = ag.Fixture(ev={}, league="ENG-PR", kickoff=ko, title="", home="", away="", home_id=1, away_id=2)
    assert ag.history_gap(states, f) is None
    f.away_id = 3                                            # 25 days behind its league
    assert ag.history_gap(states, f).startswith("team history behind")
    old = {"teams": {"1": {"league": "ENG-PR", "last": (ko - timedelta(days=60)).isoformat()},
                     "2": {"league": "ENG-PR", "last": (ko - timedelta(days=60)).isoformat()}}}
    f.away_id = 2
    assert ag.history_gap(old, f).startswith("league data stale")


# ── manage_open: the tape read decides, and a failed read decides nothing ────

class _Cur:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))


class _Conn:
    def __init__(self):
        self.sql = []

    def cursor(self, **kw):
        return _Cur(self.sql)


NOW = datetime(2026, 11, 6, 19, 5, tzinfo=timezone.utc)


def _order(ko, **kw):
    o = {"id": 63, "token_id": "Y", "condition_id": "0xc", "bid_price": 0.24, "status": "resting",
         "kickoff": ko, "created_at": ko - timedelta(hours=30), "touched_at": None,
         "paper_trade_id": None, "close_mid": None, "close_bid": None, "close_ask": None,
         "close_at": None, "team_name": "Metz"}
    o.update(kw)
    return o


def _run(monkeypatch, order, trades, book=None):
    conn, asked, written = _Conn(), [], []
    monkeypatch.setattr(ag, "open_orders", lambda c, sid: [order])
    monkeypatch.setattr(ag, "fetch_books", lambda toks: {"Y": book})
    monkeypatch.setattr(ag, "fetch_trades", lambda cid, a, b: asked.append((cid, a, b)) or trades)
    monkeypatch.setattr(ag, "write_trade", lambda c, sid, o, at: written.append((o["fill_rule"], at)) or 901)
    ag.manage_open(conn, 1, NOW)
    return conn.sql, asked, written


def _expired(sql):
    return any("status = 'expired'" in s for s, _ in sql)


def test_after_kick_off_a_failed_trades_read_leaves_the_bid_resting(monkeypatch):
    sql, asked, written = _run(monkeypatch, _order(NOW - timedelta(minutes=5)), None)
    assert asked and not written and not _expired(sql)


def test_after_kick_off_the_tape_up_to_kick_off_is_read_before_expiry(monkeypatch):
    ko = NOW - timedelta(minutes=5)
    sql, asked, written = _run(monkeypatch, _order(ko), [], book=_bk(0.20, 0.22))
    assert asked[0][1:] == ((ko - timedelta(hours=30)).timestamp(), ko.timestamp())
    assert not written and _expired(sql)          # the book after kick-off fills nothing


def test_a_print_through_the_bid_before_kick_off_fills_late_with_the_stored_close(monkeypatch):
    ko = NOW - timedelta(minutes=5)
    t = ko.timestamp() - 120
    o = _order(ko, close_mid=0.26, close_bid=0.25, close_ask=0.27, close_at=ko - timedelta(minutes=10))
    sql, _, written = _run(monkeypatch, o, [{"token_id": "Y", "price": 0.23, "timestamp": t}])
    assert written == [("traded_through", datetime.fromtimestamp(t, timezone.utc))]
    assert not _expired(sql)
    close = [p for s, p in sql if s.startswith("UPDATE paper_trades SET pm_closing_price")]
    assert close and close[0][0] == 0.26 and close[0][-1] == 901


def test_before_kick_off_a_failed_read_still_lets_the_close_be_taken(monkeypatch):
    ko = NOW + timedelta(minutes=10)
    sql, _, written = _run(monkeypatch, _order(ko), None, book=_bk(0.20, 0.22))
    assert not written and not _expired(sql)
    assert any(s.startswith("UPDATE close_maker_orders SET close_bid") for s, _ in sql)


def test_a_bid_whose_kick_off_passed_days_ago_is_still_decided_from_the_tape(monkeypatch):
    # #53/#54: no cycle ran in their last 30 minutes, so they sat resting for days
    sql, asked, written = _run(monkeypatch, _order(NOW - timedelta(days=6)), [])
    assert asked and not written and _expired(sql)


def test_open_orders_keeps_every_resting_bid_and_settle_reaches_a_late_fill():
    import inspect
    q = inspect.getsource(ag.open_orders)
    assert "(status = 'resting'" in q                                   # no kick-off bound on a resting bid
    assert "kickoff > now() - interval '30 minutes' AND close_mid IS NULL" in q
    assert "pt.result IS NULL" in inspect.getsource(ag.settle)
