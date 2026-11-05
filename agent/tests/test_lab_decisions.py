"""lab_strategy_runner's decision log (db/087): what an agent saw and decided.

The log is only worth keeping if a skip can be priced afterwards, so these pin:
a skip names the token it would have bought; a fixture outside the agent's
universe is not a decision; an entered row is never overwritten; and the close
and the result reach skipped rows as well as entered ones.
"""

import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lab_strategy_runner as lr  # noqa: E402
from tests.test_lab_strategy_runner import _event, _view  # noqa: E402

NOW = datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc)   # 30 min before _view()'s kick-off


# ── which misses are decisions ───────────────────────────────────────────────

def test_a_fixture_outside_the_agents_universe_is_not_a_decision():
    ev = _view("Arsenal", "Chelsea")
    for why in ("league", "season", "market", "home team", "away team",
                "no Over/Under history in this league", "home team not in list",
                "away team excluded"):
        assert not lr.is_decision({}, ev, why), why
    for why in ("favourite filter", "favourite unknown", "no draw leg", "no O/U 2.5",
                "no leg for the backed team"):
        assert lr.is_decision({}, ev, why), why


def test_unconfirmed_sides_count_against_a_named_club_only_when_it_plays():
    ev = _view()                                    # ESPN did not confirm
    why = "home/away not confirmed by ESPN"
    assert lr.is_decision({"side": "home"}, ev, why)
    assert lr.is_decision({"home_team": "Arsenal"}, ev, why)
    assert not lr.is_decision({"home_team": "Liverpool"}, ev, why)
    assert not lr.is_decision({"home_team_in": ["Liverpool", "Everton"]}, ev, why)
    # An exclusion list alone does not name the agent's club.
    assert lr.is_decision({"home_team_not_in": ["Liverpool"]}, ev, why)


# ── a skip names what it would have bought ──────────────────────────────────

def test_would_buy_names_the_leg_a_failed_condition_kept_it_from():
    ev = _view("Arsenal", "Chelsea")
    # Backing the home underdog: Arsenal is the favourite, so pick() refuses ...
    spec = {"market": "1x2", "side": "home", "fav_status": "underdog"}
    assert lr.pick(spec, ev)[0] is None
    # ... and the log still knows it was Arsenal's token at 0.55.
    leg, label = lr.would_buy(spec, ev)
    assert leg["token_id"] == "a1" and leg["mid"] == 0.55
    assert label == "Arsenal to win — Arsenal vs. Chelsea"
    assert lr.would_buy({"market": "ou25", "side": "under"}, ev)[0]["token_id"] == "u"
    assert lr.would_buy({"market": "1x2", "side": "draw"}, ev)[0]["token_id"] == "d1"
    # No confirmed sides: no team, so no token, never a guess.
    assert lr.would_buy({"market": "1x2", "side": "home"}, _view()) == (None, "")


def test_a_row_carries_the_price_the_book_and_the_form_it_was_decided_on():
    ev = _view("Arsenal", "Chelsea")
    ev.title = "Arsenal vs. Chelsea - More Markets"
    leg = ev.markets["draw"]
    book = {"best_bid": 0.24, "best_ask": 0.31, "ask_depth_usd": 80.0}
    forms = (lr.TeamForm(rest_days=3, form_pts5=10), None)
    r = lr.decision_row(7, {"side": "draw"}, ev, "book", "spread 0.07 > 0.05", NOW,
                        leg, "Draw — x", book, forms)
    assert r["event_title"] == "Arsenal vs. Chelsea"          # one fixture, one row
    assert r["leg_side"] == "draw" and r["token_id"] == "d1" and r["condition_id"] == "0xd"
    assert r["minutes_to_ko"] == 30.0
    assert (r["gamma_mid"], r["bid"], r["ask"], r["ask_depth_usd"]) == (0.25, 0.24, 0.31, 80.0)
    assert r["form"]["home"]["form_pts5"] == 10 and r["form"]["away"] is None


def test_the_furthest_stage_wins_when_a_fixture_is_seen_twice():
    ev = _view("Arsenal", "Chelsea")
    a = lr.decision_row(1, {"side": "draw"}, ev, "rule", "no draw leg", NOW)
    b = lr.decision_row(1, {"side": "draw"}, ev, "book", "spread", NOW)
    c = lr.decision_row(1, {"side": "over"}, ev, "rule", "no O/U 2.5", NOW)
    out = lr.merge_decisions([b, a, c])
    assert len(out) == 2
    assert {r["leg_side"]: r["stage"] for r in out} == {"draw": "book", "over": "rule"}


# ── the writes ───────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.conn.rows.pop(0) if self.conn.rows else []


class _Conn:
    def __init__(self, rows=()):
        self.sql, self.rows = [], list(rows)

    def cursor(self, *a, **k):
        return _Cur(self)


def _no_txn(monkeypatch):
    monkeypatch.setattr(lr.db_txn, "atomic", lambda conn: contextlib.nullcontext())


def test_the_upsert_never_overwrites_a_row_that_entered(monkeypatch):
    _no_txn(monkeypatch)
    sent = {}

    def fake_values(cur, sql, values, template=None):
        sent.update(sql=" ".join(sql.split()), values=values, template=template)
    monkeypatch.setattr(lr.psycopg2.extras, "execute_values", fake_values)
    ev = _view("Arsenal", "Chelsea")
    rows = [lr.decision_row(1, {"side": "home"}, ev, "form", "home form_pts5 4 < 7", NOW,
                            ev.markets["win"]["Arsenal"], "x", forms=(lr.TeamForm(form_pts5=4), None))]
    assert lr.write_decisions(_Conn(), rows) == 1
    assert sent["sql"].endswith("WHERE lab_decisions.stage <> 'entered'")
    assert "ON CONFLICT (strategy_id, event_title, kickoff, leg_side)" in sent["sql"]
    row = dict(zip(lr.DECISION_COLS, sent["values"][0]))
    assert json.loads(row["form"])["home"]["form_pts5"] == 4
    assert "%s::jsonb" in sent["template"]
    assert lr.write_decisions(_Conn(), []) == 0


def test_closes_reach_skipped_rows_and_the_agents_own_trades(monkeypatch):
    _no_txn(monkeypatch)
    now = datetime.now(timezone.utc)
    fresh, old = now - timedelta(minutes=40), now - timedelta(hours=8)
    conn = _Conn(rows=[[("tok_a", fresh), ("tok_b", fresh), ("tok_c", old)]])
    closes = {"tok_a": (int(fresh.timestamp()) - 60, 0.42)}
    n = lr.fill_closes(conn, get_close=lambda tok, ts, session=None: closes.get(tok))
    assert n == 1
    writes = [s for s, _ in conn.sql if s.startswith("UPDATE")]
    # tok_a: the log and the trade both get the close; the trade gets its CLV.
    assert any("UPDATE lab_decisions SET close_price" in s for s in writes)
    assert any("UPDATE paper_trades" in s and "pm_clv" in s for s in writes)
    # tok_b has no point yet but kicked off 40 min ago: asked again next run.
    # tok_c, 8 hours on, is given up on, so it is not asked every half hour.
    gave_up = [p for s, p in conn.sql if "close_source = 'none'" in s]
    assert gave_up == [("tok_c", old)]


def test_results_reach_skipped_rows_reusing_what_settle_already_fetched(monkeypatch):
    _no_txn(monkeypatch)
    sent = {}
    monkeypatch.setattr(lr.psycopg2.extras, "execute_values",
                        lambda cur, sql, values: sent.update(values=values))
    asked = []

    def winner(cid):
        asked.append(cid)
        return {"d1": False, "d2": True} if cid == "0xd" else None
    conn = _Conn(rows=[[("0xa",), ("0xd",), ("0xq",)]])
    lr.fill_decision_results(conn, known={"0xa": {"a1": True, "a2": False}}, get_winner=winner)
    assert asked == ["0xd", "0xq"]                  # 0xa came from settle()
    assert sorted(sent["values"]) == sorted([(True, "a1", "0xa"), (False, "a2", "0xa"),
                                             (False, "d1", "0xd"), (True, "d2", "0xd")])


# ── one cycle, end to end ────────────────────────────────────────────────────

def test_a_cycle_logs_the_skips_and_the_entry_and_trades_exactly_as_before(monkeypatch):
    raw = dict(_event(), tags=[{"label": "EPL", "slug": "epl"}],
               startTime="2026-09-12T14:00:00Z")
    strategies = [
        {"id": 1, "name": "home dogs", "theory": None,
         "spec": {"market": "1x2", "side": "home", "fav_status": "underdog"}},   # rule skip
        {"id": 2, "name": "cheap draws", "theory": None,
         "spec": {"market": "1x2", "side": "draw", "odds_max": 3.0}},            # book skip
        {"id": 3, "name": "overs", "theory": None,
         "spec": {"market": "ou25", "side": "over"}},                            # entered
        {"id": 4, "name": "serie a", "theory": None,
         "spec": {"market": "1x2", "side": "draw", "leagues": ["ITA-SA"]}},      # not its league
    ]
    books = {"d1": {"best_bid": 0.24, "best_ask": 0.25, "ask_depth_usd": 500.0},
             "o": {"best_bid": 0.51, "best_ask": 0.52, "ask_depth_usd": 900.0}}
    written, logged = [], []
    monkeypatch.setattr(lr, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    monkeypatch.setattr(lr, "_conn", lambda: object())
    monkeypatch.setattr(lr, "running_strategies", lambda conn: strategies)
    monkeypatch.setattr(lr, "fetch_upcoming", lambda now: [raw])
    monkeypatch.setattr(lr, "espn_around", lambda code, ko, cache: [])
    monkeypatch.setattr(lr, "orientation", lambda h, a, fx: ("same", None))
    monkeypatch.setattr(lr, "already_in", lambda conn, sid, cids: False)
    monkeypatch.setattr(lr, "_fetch_book", lambda leg: books[leg["token_id"]])
    monkeypatch.setattr(lr, "write_trade", lambda conn, s, ev, leg, label, book: written.append(
        (s["id"], leg["token_id"])) or 99)
    monkeypatch.setattr(lr, "write_decisions", lambda conn, rows: logged.extend(rows) or len(rows))

    assert lr.run_once() == 1
    assert written == [(3, "o")]
    by = {r["strategy_id"]: r for r in logged}
    assert set(by) == {1, 2, 3}
    assert (by[1]["stage"], by[1]["reason"], by[1]["token_id"]) == ("rule", "favourite filter", "a1")
    assert by[2]["stage"] == "book" and by[2]["reason"] == "odds 4.00 > 3.0" and by[2]["ask"] == 0.25
    assert (by[3]["stage"], by[3]["paper_trade_id"], by[3]["token_id"]) == ("entered", 99, "o")


def test_a_dry_run_writes_no_log(monkeypatch):
    monkeypatch.setattr(lr, "_conn", lambda: object())
    monkeypatch.setattr(lr, "running_strategies", lambda conn: [
        {"id": 1, "name": "x", "theory": None, "spec": {"market": "1x2", "side": "draw"}}])
    monkeypatch.setattr(lr, "fetch_upcoming", lambda now: [])
    monkeypatch.setattr(lr, "write_decisions", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert lr.run_once(dry_run=True) == 0
