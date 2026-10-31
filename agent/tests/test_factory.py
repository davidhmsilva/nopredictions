"""The factory's contract: a spec fires once per fixture at its first qualifying
moment, pays the taker fee on the way in and out, never sees the future, and a
batch reports how much of what it found is luck."""
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from factory import engine, grids                                    # noqa: E402
from factory import universes as uv                                  # noqa: E402
from factory.spec import SpecError, normalize, spec_hash             # noqa: E402

U = uv.Universe("t_inplay", "test tape", "pm_clob", None, True,
                ("minute", "goals_total", "league", "odds", "spread", "depth"),
                exits=("hold", "cash_out"), split="2026-09-01", min_n_train=1, min_n_test=1)


@pytest.fixture(autouse=True)
def _register_test_universe():
    uv.UNIVERSES[U.name] = U
    yield
    uv.UNIVERSES.pop(U.name, None)


COLS = ["fixture", "ts", "minute", "goals_total", "league", "ask", "bid", "depth", "token_id", "won"]


def frame(rows):
    df = pd.DataFrame(rows, columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["condition_id"], df["label"] = "c", "x"
    df["source_row_id"] = range(len(df))
    return engine.make_frame(U, df.sort_values(["fixture", "ts"]).reset_index(drop=True))


def spec(**kw):
    base = {"universe": U.name, "where": [], "price": {}}
    base.update(kw)
    return normalize(base)


def test_one_entry_per_fixture_at_the_first_qualifying_minute():
    fr = frame([["A", "2026-09-05 15:00", 60, 0, "L", 0.50, 0.49, 500, "T", 1.0],
                ["A", "2026-09-05 15:10", 70, 0, "L", 0.40, 0.39, 500, "T", 1.0],
                ["A", "2026-09-05 15:15", 75, 0, "L", 0.35, 0.34, 500, "T", 1.0],
                ["B", "2026-09-05 16:10", 71, 1, "L", 0.45, 0.44, 500, "U", 0.0]])
    s = spec(where=[["minute", ">=", 65]])
    e = engine.entries(fr, engine.mask(s, fr))
    assert list(fr.df.loc[e, "minute"]) == [70, 71]


def test_return_is_net_of_the_taker_fee():
    fr = frame([["A", "2026-09-05", 60, 0, "L", 0.50, 0.49, 500, "T", 1.0],
                ["B", "2026-09-05", 60, 0, "L", 0.50, 0.49, 500, "U", 0.0]])
    ret, _ = engine.returns(fr, np.array([0, 1]), {"type": "hold"})
    assert ret[0] == pytest.approx(1 / (0.5 * 1.025) - 1)
    assert ret[1] == pytest.approx(-1.0)


def test_cash_out_sells_at_the_bid_minus_fee_and_falls_back_to_hold_off_tape():
    fr = frame([["A", "2026-09-05 15:00", 60, 0, "L", 0.50, 0.49, 500, "T", 1.0],
                ["A", "2026-09-05 15:10", 70, 0, "L", 0.31, 0.30, 500, "T", 1.0],
                ["B", "2026-09-05 15:00", 60, 0, "L", 0.50, 0.49, 500, "V", 1.0]])
    out = {"type": "cash_out", "minute": 70}
    ret, fb = engine.returns(fr, np.array([0, 2]), out)
    assert ret[0] == pytest.approx(0.30 * (1 - 0.05 * 0.70) / (0.5 * 1.025) - 1)
    assert ret[1] == pytest.approx(1 / (0.5 * 1.025) - 1) and fb == 1     # token V never re-quoted


def test_price_gates_and_nan_never_satisfy_a_condition():
    fr = frame([["A", "2026-09-05", 60, None, "L", 0.50, 0.40, 500, "T", 1.0],   # wide, no goals value
                ["B", "2026-09-05", 60, 0, "L", 0.50, 0.49, 10, "U", 1.0],       # thin
                ["C", "2026-09-05", 60, 0, "L", 0.20, 0.19, 500, "W", 1.0],      # odds 5.0
                ["D", "2026-09-05", 60, 0, "M", 0.50, 0.49, 500, "X", 1.0]])
    s = spec(where=[["goals_total", "==", 0], ["league", "==", "L"]],
             price={"max_spread": 0.03, "min_depth_usd": 100, "odds_max": 3.0})
    assert list(fr.df.loc[engine.mask(s, fr), "fixture"]) == []
    s2 = spec(where=[["league", "==", "M"]], price={"max_spread": 0.03})
    assert list(fr.df.loc[engine.mask(s2, fr), "fixture"]) == ["D"]


def test_unsettled_rows_are_tradeable_live_but_not_backtestable():
    fr = frame([["A", "2026-09-05", 60, 0, "L", 0.50, 0.49, 500, "T", np.nan]])
    s = spec()
    assert not engine.mask(s, fr).any() and engine.mask(s, fr, require_won=False).all()


def test_train_test_split_is_by_fixture_date():
    fr = frame([["A", "2026-08-20", 60, 0, "L", 0.5, 0.49, 500, "T", 1.0],
                ["B", "2026-09-05", 60, 0, "L", 0.5, 0.49, 500, "U", 0.0]])
    assert list(fr.train) == [True, False]
    r = engine.backtest_one(spec(), fr)
    assert r["train"]["n"] == 1 and r["test"]["n"] == 1


def test_bh_qvalues():
    q = engine.bh_qvalues([0.01, 0.04, 0.03, 0.5])
    assert q == pytest.approx([0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.5])


def test_spec_rejects_unknown_features_ops_and_exits():
    with pytest.raises(SpecError):
        spec(where=[["final_score", "==", 2]])
    with pytest.raises(SpecError):
        spec(where=[["minute", "~", 2]])
    with pytest.raises(SpecError):
        normalize({"universe": "soccer_settled", "exit": {"type": "cash_out", "minute": 80}})


def test_the_open_universe_cannot_see_the_close():
    with pytest.raises(SpecError):
        normalize({"universe": "soccer_prematch_open", "market": "1x2", "side": "home",
                   "where": [["move", ">=", 0.02]]})
    normalize({"universe": "soccer_prematch_close", "market": "1x2", "side": "home",
               "where": [["move", ">=", 0.02]]})


def test_prematch_needs_a_consistent_market_and_side():
    with pytest.raises(SpecError):
        normalize({"universe": "nfl_prematch", "market": "total", "side": "home"})


def test_hash_ignores_the_name():
    a = spec(name="one", where=[["minute", ">=", 65]])
    b = spec(name="two", where=[["minute", ">=", 65]])
    assert spec_hash(a) == spec_hash(b)


def test_grids_generate_valid_unique_specs():
    specs = grids.all_specs()
    assert len(specs) > 5000
    assert len({spec_hash(s) for s in specs}) == len(specs)


@pytest.mark.skipif(not os.path.exists(uv.GAMES_CSV), reason="nflverse games.csv not cached")
def test_nfl_tape_is_sane():
    df = uv.load_nfl_prematch()
    sp = df[(df.market == "spread") & df.won.notna()]
    ml = df[(df.market == "ml") & (df.side == "home") & df.won.notna()]
    assert 0.46 < sp.won.mean() < 0.54                       # spreads cover about half the time
    assert 0.52 < ml.won.mean() < 0.60                       # home teams win a bit more than half
    # the proxy price is calibrated: implied ≈ realised on the moneyline
    assert abs(ml.prob.mean() - ml.won.mean()) < 0.02


# Every column any live loader reads, as psycopg2 hands back a query with no
# rows: the names, dtype object, nothing in them.
_LIVE_COLS = ["source_row_id", "fixture_id", "ts", "minute", "home_goals", "away_goals",
              "goals_total", "league", "event_title", "question", "fav_side", "fav_team",
              "home_possession", "fair_base", "token_id", "condition_id", "bid", "ask", "depth",
              "obs_version", "won_raw"]


def _sql_returning(rows):
    def fake(sql, conn, params=None):
        return pd.DataFrame(rows, columns=_LIVE_COLS) if rows else \
            pd.DataFrame({c: pd.Series([], dtype=object) for c in _LIVE_COLS})
    return fake


@pytest.mark.parametrize("name", [n for n, u in uv.UNIVERSES.items() if u.live and n != "t_inplay"])
def test_live_loaders_survive_a_quiet_minute(monkeypatch, name):
    """No live match in the window is the normal state most of the day. The
    next-goal loader built its label from an empty float column and raised,
    which failed the whole factory-run job every quiet minute from 09-28 to
    10-05 (7,729 times)."""
    monkeypatch.setattr(uv.pd, "read_sql_query", _sql_returning([]))
    df = uv.UNIVERSES[name].load(None, mode="live", live_minutes=4)
    assert df.empty and "label" in df


def test_next_goal_label_reads_the_line_and_the_match(monkeypatch):
    row = dict.fromkeys(_LIVE_COLS)
    row.update(source_row_id=1, fixture_id=7, ts="2026-10-05T20:00:00Z", minute=80, home_goals=1,
               away_goals=0, goals_total=1, event_title="A vs. B", bid=0.4, ask=0.42, won_raw=None)
    monkeypatch.setattr(uv.pd, "read_sql_query", _sql_returning([[row[c] for c in _LIVE_COLS]]))
    df = uv.load_next_goal(None, mode="live")
    assert df["label"].tolist() == ["Over 1.5 (next goal) — A vs. B"]


# ── settling: read only what can settle ──────────────────────────────────────

class _SettleConn:
    pass


def _settle_setup(monkeypatch, frame, ready, recorded):
    from factory import registry, runner

    trades = [dict(id=i, universe="t_inplay", fixture=f"af:{fx}", source_row_id=row, stake=1.0,
                   spec={"exit": {"type": "hold"}}) for i, fx, row in frame]
    monkeypatch.setattr(uv.UNIVERSES["t_inplay"], "source_table", "pressure_observations")
    monkeypatch.setattr(registry, "pending", lambda conn: trades)
    monkeypatch.setattr(registry, "ready_rows", lambda conn, table, ids: set(ready))
    monkeypatch.setattr(registry, "settle_trade", lambda conn, tid, res, ret, st: recorded.append((tid, res)))
    monkeypatch.setattr(registry, "refresh_forward", lambda conn: None)
    monkeypatch.setattr(registry, "apply_promotion", lambda conn: [])
    return runner


def test_a_trade_with_no_outcome_does_not_load_its_fixture(monkeypatch):
    """Trades that never got an outcome pulled their whole fixtures off the tape
    every 10 minutes: 214 MB a call on 2026-10-31. If nothing is ready, nothing loads."""
    loads, recorded = [], []
    runner = _settle_setup(monkeypatch, [(1, 7, 100), (2, 8, 200)], ready=[], recorded=recorded)
    monkeypatch.setattr(uv.UNIVERSES["t_inplay"], "load", lambda *a, **k: loads.append(k), raising=False)
    assert runner.settle(_SettleConn()) == 0
    assert loads == [] and recorded == []


def test_only_the_ready_fixtures_are_loaded(monkeypatch):
    loads, recorded = [], []
    runner = _settle_setup(monkeypatch, [(1, 7, 100), (2, 8, 200)], ready=[100], recorded=recorded)

    def load(conn, mode, fixture_ids, keep_rows):
        loads.append((mode, fixture_ids, keep_rows))
        return pd.DataFrame({"source_row_id": [100], "fixture": ["af:7"], "ts": pd.to_datetime(["2026-10-30"], utc=True),
                             "minute": [80.0], "ask": [0.5], "bid": [0.48], "won": [1.0], "token_id": ["t"]})

    monkeypatch.setattr(uv.UNIVERSES["t_inplay"], "load", load, raising=False)
    assert runner.settle(_SettleConn()) == 1
    assert loads == [("settle", [7], {100})]
    assert recorded == [(1, "won")]


def test_the_row_a_trade_was_bought_on_survives_the_minute_dedupe():
    """The ESPN feed repeats minutes. A bot can enter on the second poll of a minute,
    and the settle read then dropped that row: the trade could never close."""
    df = pd.DataFrame({"source_row_id": [1, 2, 3], "fixture": ["af:7"] * 3, "minute": [80, 80, 80],
                       "ts": ["2026-10-30T20:00:00Z", "2026-10-30T20:00:40Z", "2026-10-30T20:00:50Z"]})
    assert uv._finish(df.copy(), dedupe=["fixture", "minute"])["source_row_id"].tolist() == [1]
    kept = uv._finish(df.copy(), dedupe=["fixture", "minute"], keep_rows={2})
    assert kept["source_row_id"].tolist() == [1, 2]
