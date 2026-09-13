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
