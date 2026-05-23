"""
Demo + smoke test for the Monte Carlo simulator.

Run:
  cd agent && source ../ingest/.venv/bin/activate
  python sim_demo.py                       # default: Arsenal vs Chelsea
  python sim_demo.py "Real Madrid" Barcelona

Outputs:
  1. Calibration check — sim (pure) must match closed-form Poisson within 1pp.
  2. Effect of adjustments — sim (adjusted) vs Poisson, market by market.
  3. Real-team example — DC lambdas → sim → full market sheet vs DC.
  4. Timing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make agent/ importable when run directly
sys.path.insert(0, str(Path(__file__).parent))

from dixon_coles import DixonColesModel  # noqa: E402
from sim.calibrator import calibrate_pure_sim, compare_adjusted_vs_poisson  # noqa: E402
from sim.pricer import price_markets  # noqa: E402
from sim.simulator import SimConfig, simulate  # noqa: E402


PARAMS_PATH = Path(__file__).parent / "dc_model_params.json"


# ── Pretty printing ──────────────────────────────────────────────────


def _fmt_pct(x: float) -> str:
    return f"{x * 100:5.2f}%"


def print_calibration() -> bool:
    print("=" * 72)
    print(" 1. CALIBRATION CHECK (pure sim vs closed-form Poisson)")
    print("=" * 72)
    cal = calibrate_pure_sim(n_sims=100_000, tol_pp=1.0)
    status = "PASS" if cal["passed"] else "FAIL"
    print(f"\n  Max deviation: {cal['max_deviation_pp']:.3f}pp"
          f"  (tolerance {cal['tolerance_pp']}pp)  →  {status}\n")
    print(f"  {'lambdas':<14}{'home_win':>10}{'draw':>10}{'away_win':>10}"
          f"{'over_2_5':>10}{'btts':>10}{'max':>10}")
    print("  " + "-" * 74)
    for p in cal["pairs"]:
        lh, la = p["lambdas"]
        d = p["diff_pp"]
        print(f"  ({lh:.1f},{la:.1f})    "
              f"{d['home_win']:+9.3f} {d['draw']:+9.3f} {d['away_win']:+9.3f} "
              f"{d['over_2_5']:+9.3f} {d['btts']:+9.3f}  {p['max_diff_pp']:8.3f}")
    print()
    return cal["passed"]


def print_adjustments_effect() -> None:
    print("=" * 72)
    print(" 2. EFFECT OF STATE-DEP ADJUSTMENTS (adjusted sim − Poisson, in pp)")
    print("=" * 72)
    cmp = compare_adjusted_vs_poisson(n_sims=50_000)
    print(f"  {'lambdas':<14}{'home_win':>10}{'draw':>10}{'away_win':>10}"
          f"{'over_2_5':>10}{'btts':>10}{'Δ goals':>10}")
    print("  " + "-" * 74)
    for p in cmp["pairs"]:
        lh, la = p["lambdas"]
        d = p["diff_pp"]
        g_diff = p["exp_total_goals_sim"] - p["exp_total_goals_poi"]
        print(f"  ({lh:.1f},{la:.1f})    "
              f"{d['home_win']:+9.3f} {d['draw']:+9.3f} {d['away_win']:+9.3f} "
              f"{d['over_2_5']:+9.3f} {d['btts']:+9.3f}  {g_diff:+9.3f}")
    print("\n  Expected directional pattern:")
    print("    - one-sided lambdas → small ↑ in draws (leader sits, trailing pushes)")
    print("    - Δ goals ~ +0.05–0.15 (4 mins stoppage + dynamic pushing)")
    print()


def demo_real_match(home: str, away: str) -> None:
    print("=" * 72)
    print(f" 3. REAL MATCH:  {home}  vs  {away}")
    print("=" * 72)

    if not PARAMS_PATH.exists():
        print(f"  (skipped — no DC params at {PARAMS_PATH})\n")
        return

    model = DixonColesModel.load(str(PARAMS_PATH))
    try:
        dc = model.predict(home, away)
    except ValueError as e:
        print(f"  Skipped — {e}\n")
        return

    lh, la = dc["lambda_home"], dc["lambda_away"]
    print(f"\n  DC lambdas:  λ_home = {lh:.3f}    λ_away = {la:.3f}\n")

    t0 = time.perf_counter()
    sim_res = simulate(lh, la, config=SimConfig(n_sims=50_000, seed=42))
    sim_p = price_markets(sim_res)
    t_ms = (time.perf_counter() - t0) * 1000

    print(f"  {'Market':<14}{'DC':>10}{'Sim':>10}{'Δpp':>10}")
    print("  " + "-" * 44)
    for mkt in ["home_win", "draw", "away_win", "over_1_5",
                "over_2_5", "over_3_5", "btts"]:
        dc_v = dc.get(mkt, float("nan"))
        s = sim_p.get(mkt, float("nan"))
        d_pp = (s - dc_v) * 100
        print(f"  {mkt:<14}{_fmt_pct(dc_v):>10}{_fmt_pct(s):>10}{d_pp:+10.2f}")

    print(f"\n  Expected goals:  H {sim_p['exp_home_goals']:.2f}   "
          f"A {sim_p['exp_away_goals']:.2f}   "
          f"Total {sim_p['exp_total_goals']:.2f}")

    print(f"\n  Top 5 exact scores:")
    for hs, as_, p in sim_p["top_scores"][:5]:
        print(f"    {hs}-{as_}: {_fmt_pct(p)}")

    print(f"\n  Half-time:  H {_fmt_pct(sim_p['ht_home_win'])}   "
          f"D {_fmt_pct(sim_p['ht_draw'])}   "
          f"A {_fmt_pct(sim_p['ht_away_win'])}")

    print(f"\n  HT/FT (top 5):")
    htft = sorted(sim_p["htft"].items(), key=lambda x: -x[1])[:5]
    for k, v in htft:
        print(f"    {k}: {_fmt_pct(v)}")

    print(f"\n  Sim wall time: {t_ms:.0f} ms for 50k sims "
          f"({sim_res.n_minutes_simulated} minutes each)")
    print()


def main() -> int:
    home = sys.argv[1] if len(sys.argv) > 1 else "Arsenal"
    away = sys.argv[2] if len(sys.argv) > 2 else "Chelsea"

    passed = print_calibration()
    print_adjustments_effect()
    demo_real_match(home, away)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
