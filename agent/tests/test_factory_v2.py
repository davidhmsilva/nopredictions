"""Factory v2's contract: clustered tests that keep their size, shrinkage that
pulls noise in, a power gate that refuses the unresolvable, maker questions
that are fixed by construction refused, a holdout only `confirm` reads and
every read of it on the ledger, and team state that never sees its own match."""
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import close_model as cm                                            # noqa: E402
from factory.v2 import families as fm                               # noqa: E402
from factory.v2 import protocol, search, stats                      # noqa: E402
from factory.v2 import tape                                         # noqa: E402


# ── stats ───────────────────────────────────────────────────────────────────

def test_ols_cluster_recovers_coefficients():
    rng = np.random.default_rng(1)
    n = 4000
    x = rng.normal(size=n)
    g = np.repeat(np.arange(n // 2), 2)
    y = 0.5 + 2.0 * x + rng.normal(size=n)
    beta, V = stats.ols_cluster(y, np.column_stack([np.ones(n), x]), g)
    assert beta[1] == pytest.approx(2.0, abs=0.08)
    assert math.sqrt(V[1, 1]) < 0.05


def test_clustering_widens_the_error_when_rows_share_a_shock():
    rng = np.random.default_rng(2)
    G, per = 300, 10
    g = np.repeat(np.arange(G), per)
    x = np.repeat(rng.normal(size=G), per)            # a match-level feature
    y = np.repeat(rng.normal(size=G), per) + 0.1 * rng.normal(size=G * per)
    X = np.column_stack([np.ones(G * per), x])
    _, Vc = stats.ols_cluster(y, X, g)
    _, Vn = stats.ols_cluster(y, X, np.arange(G * per))
    assert Vc[1, 1] > 3 * Vn[1, 1]


def test_wald_is_uniform_under_the_null_and_small_under_an_effect():
    rng = np.random.default_rng(3)
    ps = []
    for _ in range(200):
        n = 400
        X = np.column_stack([np.ones(n), rng.normal(size=(n, 2))])
        b, V = stats.ols_cluster(rng.normal(size=n), X, np.arange(n))
        ps.append(stats.wald(b, V, [1, 2])[2])
    assert 0.02 < np.mean(np.array(ps) < 0.05) < 0.10
    n = 2000
    X = np.column_stack([np.ones(n), rng.normal(size=(n, 2))])
    y = 0.2 * X[:, 1] + rng.normal(size=n)
    b, V = stats.ols_cluster(y, X, np.arange(n))
    assert stats.wald(b, V, [1, 2])[2] < 1e-6


def test_eb_shrinks_noise_toward_the_mean_and_flattens_when_there_is_no_spread():
    means = np.array([0.10, -0.02, 0.0, 0.01, -0.01])
    ses = np.array([0.08, 0.005, 0.005, 0.005, 0.005])
    post, _, mu, tau2 = stats.eb_shrink(means, ses)
    assert abs(post[0] - mu) < abs(means[0] - mu)          # the noisy one moves the most
    flat, _, mu0, t0 = stats.eb_shrink(np.array([0.01, 0.011, 0.009]), np.array([0.05, 0.05, 0.05]))
    assert t0 == 0.0 and np.allclose(flat, mu0)


def test_mde_scales_with_the_standard_error():
    assert stats.mde(0.01) == pytest.approx(2.486 * 0.01, rel=1e-3)
    assert stats.mde(None) == float("inf")


def test_costs_by_execution():
    p0, o0 = np.array([0.5]), np.array([1.95])
    assert stats.cost(p0, o0, "pin_raw")[0] == pytest.approx(1 / 1.95)
    taker = stats.cost(p0, o0, "pm_taker")[0]
    assert taker == pytest.approx(0.506 * (1 + 0.05 * 0.494))
    assert stats.cost(p0, o0, "pm_maker_loose")[0] == pytest.approx(0.494)


def test_clv_is_value_at_the_close_and_beat_close_is_the_raw_ratio():
    v = stats.per_bet("clv", "pin_raw", [0.5], [2.1], [0.5], [1], [2.0])
    assert v[0] == pytest.approx(0.5 * 2.1 - 1)
    b = stats.per_bet("beat_close", "pin_raw", [0.5], [2.1], [0.5], [1], [2.0])
    assert b[0] == pytest.approx(2.1 / 2.0 - 1)


def test_questions_fixed_by_construction_are_refused():
    with pytest.raises(ValueError):
        stats.check("clv", "pm_maker_loose")
    with pytest.raises(ValueError):
        stats.check("clv", "pm_maker_strict")
    with pytest.raises(ValueError):
        stats.check("beat_close", "pm_taker")
    stats.check("yield", "pm_maker_strict")


def test_a_strict_maker_bid_only_fills_when_the_close_reaches_it():
    p0 = np.array([0.50, 0.50])
    p1 = np.array([0.52, 0.48])                         # the second drifted through the bid (0.494)
    v = stats.per_bet("yield", "pm_maker_strict", p0, [2, 2], p1, [1, 1])
    assert np.isnan(v[0]) and v[1] == pytest.approx(1 / 0.494 - 1)


# ── a synthetic tape ────────────────────────────────────────────────────────

def synth(n_matches=6000, signal=0.0, seed=0):
    """Two sides per match; move depends on x_mres by `signal`."""
    rng = np.random.default_rng(seed)
    seasons = rng.choice(np.arange(2012, 2026), n_matches)
    rows = []
    for i in range(n_matches):
        p = rng.uniform(0.2, 0.7)
        x = rng.normal()
        for side, sgn in (("home", 1), ("away", -1)):
            p0 = p if side == "home" else 1 - p - 0.25
            p0 = min(max(p0, 0.05), 0.9)
            l0 = math.log(p0 / (1 - p0))
            l1 = l0 + signal * sgn * x + rng.normal(scale=0.1)
            p1 = 1 / (1 + math.exp(-l1))
            rows.append({"match_id": i, "season": float(seasons[i]), "view": "1x2_sides", "side": side,
                         "market": "1x2", "p0": p0, "p1": p1, "o0": 1 / (p0 * 1.025), "o1": 1 / (p1 * 1.02),
                         "won": float(rng.random() < p1), "l0": l0, "l1": l1, "move": l1 - l0,
                         "is_home": 1.0 if side == "home" else 0.0, "x_mres": sgn * x,
                         "league_code": "ENG-PR", "tier": 1.0, "month": 10.0})
    df = pd.DataFrame(rows)
    df["split"] = tape.split_of("1x2", df.season.to_numpy())
    return df


def test_splits_are_fixed_by_season_and_market():
    s = tape.split_of("1x2", np.array([2012, 2018, 2019, 2021, 2022, 2025]))
    assert list(s) == ["train", "train", "validation", "validation", "holdout", "holdout"]
    o = tape.split_of("ou25", np.array([2018, 2019, 2021, 2022]))
    assert list(o) == [None, "train", "validation", "holdout"]


def test_a_family_with_signal_is_found_and_projected_out_of_sample():
    df = synth(signal=0.05)
    fam = fm.Family("t", "test", {"1x2_sides": ["x_mres"]})
    tr, va = protocol.development(df)
    fit = fm.fit_family(tr, "1x2_sides", "move", fam, ["ENG-PR"])
    assert fit["p"] < 1e-10
    proj = fm.project(va, "1x2_sides", "move", fam, ["ENG-PR"], fit["beta_f"])
    assert proj["gamma"] == pytest.approx(1.0, abs=0.25) and proj["p"] < 1e-6


def test_a_family_without_signal_is_not():
    df = synth(signal=0.0, seed=4)
    fam = fm.Family("t", "test", {"1x2_sides": ["x_mres"]})
    tr, _ = protocol.development(df)
    assert fm.fit_family(tr, "1x2_sides", "move", fam, ["ENG-PR"])["p"] > 0.01


def test_nothing_that_searches_sees_the_holdout(monkeypatch):
    df = synth(signal=0.05)
    seen = []
    real_mask = search.mask

    def spy(d, spec):
        seen.append(set(d.split.unique()))
        return real_mask(d, spec)
    monkeypatch.setattr(search, "mask", spy)
    specs = [search._spec("t", "1x2_sides", "home", [["x_mres", ">=", 0.5]], None, ["x"])]
    tr, va = protocol.development(df)
    search.run(tr, va, specs, "clv", "pin_raw", target=1.0, k=5)
    assert seen and all("holdout" not in s for s in seen)


def test_the_power_gate_refuses_what_the_data_cannot_resolve():
    df = synth(n_matches=800, signal=0.0, seed=5)
    tr, va = protocol.development(df)
    spec = search._spec("t", "1x2_sides", "home", [], None, ["all"])
    r = search.run(tr, va, [spec], "yield", "pin_raw", target=0.01, k=5)
    assert r["gated"] == 1 and r["tested"] == 0


def test_confirm_reads_the_holdout_once_and_writes_the_ledger(tmp_path):
    df = synth(signal=0.05)
    spec = search._spec("t", "1x2_sides", "home", [["x_mres", ">=", 0.5]], None, ["x"])
    items = [{"kind": "spec", "objective": "clv", "execution": "pin_raw", "id": search.spec_id(spec),
              "spec": spec, "train": {}, "valid": {}}]
    pre = tmp_path / "prereg.json"
    pre.write_text(json.dumps({"items": items}))
    led = tmp_path / "ledger.jsonl"
    e1 = protocol.confirm(df, str(pre), str(led))
    assert e1["round"] == 1 and e1["results"][0]["n"] > 0
    e2 = protocol.confirm(df, str(pre), str(led))
    assert e2.get("from_ledger") and len(led.read_text().strip().splitlines()) == 1


# ── team state never sees its own match ─────────────────────────────────────

def test_team_state_is_read_before_the_match_updates_it():
    base = pd.DataFrame({
        "match_id": [1, 2, 3], "league": "ENG-PR", "season": 2015,
        "kickoff_utc": pd.to_datetime(["2015-08-01", "2015-08-08", "2015-08-15"], utc=True),
        "h": [10, 20, 10], "a": [20, 10, 20], "hs": [1, 0, 2], "as_": [0, 0, 1],
        "hst": [5.0, 3.0, 4.0], "ast": [2.0, 3.0, 4.0], "hsh": [10.0, 8.0, 9.0], "ash": [7.0, 8.0, 9.0],
        "cl_oh": [2.0, 2.5, 2.2], "cl_od": [3.3, 3.2, 3.3], "cl_oa": [3.8, 2.9, 3.4], "cl_bk": 5})
    f1, _ = cm.walk(base)
    changed = base.copy()
    changed.loc[2, ["hs", "as_", "hst", "ast"]] = [9, 0, 20.0, 0.0]      # only match 3's own result
    f2, _ = cm.walk(changed)
    cols = [c for c in f1.columns if c[:2] in ("h_", "a_")]
    assert np.allclose(f1.loc[2, cols].astype(float), f2.loc[2, cols].astype(float))


# ── entry time (Football-Data collects Friday / Tuesday afternoons) ─────────

def test_entry_hours_follow_the_collection_schedule():
    ts = pd.Series(pd.to_datetime([
        "2024-03-16 15:00",   # Saturday   → 24h after Friday 15:00
        "2024-03-17 16:30",   # Sunday     → 49.5h
        "2024-03-18 20:00",   # Monday     → 77h
        "2024-03-15 20:00",   # Friday     → 5h (collected the same afternoon)
        "2024-03-20 19:45",   # Wednesday  → 28.75h after Tuesday 15:00
        "2024-03-19 12:00",   # Tuesday before the collection → unknown
    ], utc=True))
    h = tape.entry_hours(ts)
    assert list(h[:5]) == pytest.approx([24.0, 49.5, 77.0, 5.0, 28.75])
    assert np.isnan(h[5])


def test_side_both_pools_home_and_away():
    df = synth(n_matches=50)
    both = search._spec("t", "1x2_sides", "both", [], None, ["x"])
    home = search._spec("t", "1x2_sides", "home", [], None, ["x"])
    assert search.mask(df, both).sum() == 2 * search.mask(df, home).sum()


def test_an_interaction_family_keeps_its_main_effects_as_controls():
    df = synth(signal=0.0, seed=6)
    rng = np.random.default_rng(6)
    df["mv_hat"] = rng.normal(scale=0.02, size=len(df))
    df["hb_days"] = rng.choice([0.0, 1.0, 2.0], size=len(df))
    df["mv_x_hb"] = df.mv_hat * df.hb_days
    df["move"] = df.move + 1.0 * df.mv_hat + 0.5 * df.mv_x_hb      # stronger the earlier
    fam = fm.Family("t", "test", {"1x2_sides": ["mv_x_hb"]}, extra={"1x2_sides": ["mv_hat", "hb_days"]})
    tr, _ = protocol.development(df)
    fit = fm.fit_family(tr, "1x2_sides", "move", fam, ["ENG-PR"])
    assert fit["beta_f"][0] == pytest.approx(0.5, abs=0.2) and fit["p"] < 1e-4
