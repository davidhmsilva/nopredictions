"""
evaluate.py — edge-measurement harness for NOPREDICTIONS strategies.

Read-only. Answers the only question that matters before risking money:
"Which of these strategies actually has edge, and how confident can we be?"

For every strategy it reports, over *settled* trades:

  1. Sample / record       — n, W/L/V, win rate
  2. Profitability         — staked, P&L, yield %, bootstrap 95% CI, p(yield>0)
  3. Calibration           — Brier score + skill vs base rate, reliability table
  4. CLV (real only)       — excludes circular "model CLV" where the closing line
                             is just our own model_probability echoed back

It also splits PAPER vs LIVE so the execution gap is visible.

Usage:
    cd agent && source ../ingest/.venv/bin/activate
    python evaluate.py                 # all strategies, full report
    python evaluate.py --strategy 5    # one strategy
    python evaluate.py --live          # live (pm_live=TRUE) trades only
    python evaluate.py --min-n 30      # only verdict strategies with >= N settled
    python evaluate.py --json          # machine-readable dump
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

try:
    from .tools.db import run_analysis_query
except ImportError:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
    from db import run_analysis_query  # type: ignore

# CLV smaller than this between closing_price and model_probability means the
# "closing line" was just our own model echoed back -> circular, not real edge.
N_BOOTSTRAP = 20_000
RNG = np.random.default_rng(42)


# ─── data ───────────────────────────────────────────────────────────────────

def _load(strategy_id: int | None, live_only: bool) -> list[dict]:
    where = ["pt.result IS NOT NULL", "pt.strategy_id != 9"]  # 9 = synthetic Live view
    if strategy_id is not None:
        where = ["pt.result IS NOT NULL", f"pt.strategy_id = {strategy_id}"]
    if live_only:
        where.append("pt.pm_live = TRUE")
    sql = f"""
        SELECT pt.strategy_id, s.name AS strategy,
               pt.result, pt.stake_units, pt.payout_units,
               pt.entry_odds, pt.entry_price, pt.model_probability,
               pt.expected_edge, pt.clv, pt.clv_source, pt.model_clv,
               pt.closing_price, pt.pm_live
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        WHERE {' AND '.join(where)}
        ORDER BY pt.strategy_id, pt.id
    """
    return run_analysis_query(sql)


def _f(v):
    return float(v) if v is not None else None


# ─── metrics ──────────────────────────────────────────────────────────────────

def _bootstrap_yield_ci(pnl: np.ndarray, stake: np.ndarray):
    """Bootstrap 95% CI on yield (= sum pnl / sum stake) and p(yield <= 0)."""
    n = len(pnl)
    if n < 2:
        return None, None, None
    idx = RNG.integers(0, n, size=(N_BOOTSTRAP, n))
    boot = pnl[idx].sum(axis=1) / stake[idx].sum(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    p_le_zero = float((boot <= 0).mean())
    return float(lo), float(hi), p_le_zero


def _brier(prob: np.ndarray, won: np.ndarray):
    """Brier score + skill score vs always-predict-base-rate."""
    if len(prob) == 0:
        return None, None, None
    brier = float(np.mean((prob - won) ** 2))
    base = float(won.mean())
    brier_base = float(np.mean((base - won) ** 2))
    skill = 1.0 - brier / brier_base if brier_base > 0 else None
    return brier, brier_base, skill


def _reliability(prob: np.ndarray, won: np.ndarray, n_bins: int = 5):
    """Equal-width reliability table over [0,1]."""
    rows = []
    edges = np.linspace(0, 1, n_bins + 1)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (prob >= lo) & (prob < hi) if i < n_bins - 1 else (prob >= lo) & (prob <= hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": int(mask.sum()),
            "pred": float(prob[mask].mean()),
            "actual": float(won[mask].mean()),
        })
    return rows


def evaluate_strategy(trades: list[dict]) -> dict:
    name = trades[0]["strategy"]
    sid = trades[0]["strategy_id"]

    won = sum(1 for t in trades if t["result"] == "won")
    lost = sum(1 for t in trades if t["result"] == "lost")
    void = sum(1 for t in trades if t["result"] == "void")

    # Profitability over decisive (non-void) trades.
    dec = [t for t in trades if t["result"] in ("won", "lost")]
    pnl = np.array([_f(t["payout_units"]) - _f(t["stake_units"]) for t in dec])
    stake = np.array([_f(t["stake_units"]) for t in dec])
    staked = float(stake.sum())
    total_pnl = float(pnl.sum())
    yld = total_pnl / staked if staked else None
    lo, hi, p_le_zero = _bootstrap_yield_ci(pnl, stake)

    # Calibration over trades with a model probability (non-void).
    cal = [t for t in dec if t["model_probability"] is not None]
    prob = np.array([_f(t["model_probability"]) for t in cal])
    wonbin = np.array([1.0 if t["result"] == "won" else 0.0 for t in cal])
    brier, brier_base, skill = _brier(prob, wonbin)
    reliability = _reliability(prob, wonbin)

    # CLV — the `clv` column is now trustworthy at source (migration 010 +
    # resolver _safe_market_clv): it is populated only for real, plausible
    # market closing lines. circular/artifact/suspect rows have clv = NULL and
    # are tagged in clv_source. So we can simply trust clv IS NOT NULL here.
    real_clv = [_f(t["clv"]) for t in trades if t["clv"] is not None]
    suspect = sum(1 for t in trades if t["clv_source"] == "suspect")
    clv_arr = np.array(real_clv) if real_clv else np.array([])
    clv_stats = None
    if len(clv_arr) >= 2:
        from scipy import stats
        t_stat, p_two = stats.ttest_1samp(clv_arr, 0.0)
        clv_stats = {
            "n": int(len(clv_arr)),
            "mean": float(clv_arr.mean()),
            "pct_positive": float((clv_arr > 0).mean()),
            "p_value": float(p_two),
            "suspect_excluded": suspect,
        }

    return {
        "id": sid, "name": name,
        "n_settled": len(trades), "won": won, "lost": lost, "void": void,
        "win_rate": won / (won + lost) if (won + lost) else None,
        "staked": staked, "pnl": total_pnl, "yield": yld,
        "yield_ci": [lo, hi], "p_yield_le_zero": p_le_zero,
        "brier": brier, "brier_base": brier_base, "brier_skill": skill,
        "reliability": reliability,
        "clv": clv_stats,
        "circular_clv_excluded": sum(1 for t in trades if t["clv_source"] == "model"),
    }


def _verdict(r: dict, min_n: int) -> str:
    """One-line edge verdict from the evidence."""
    if r["n_settled"] < min_n:
        return f"INSUFFICIENT DATA (n={r['n_settled']} < {min_n})"
    flags = []
    # CLV is king (methodological rule #5).
    if r["clv"] and r["clv"]["n"] >= 20:
        if r["clv"]["mean"] > 0 and r["clv"]["p_value"] < 0.05:
            flags.append("CLV+ (sig)")
        elif r["clv"]["mean"] < 0 and r["clv"]["p_value"] < 0.05:
            flags.append("CLV- (sig)")
        else:
            flags.append("CLV~ (flat)")
    else:
        flags.append("no real CLV")
    # Yield significance.
    p = r["p_yield_le_zero"]
    if p is not None:
        if p < 0.05:
            flags.append(f"yield+ (p={p:.3f})")
        elif p > 0.95:
            flags.append(f"yield- (p={1-p:.3f})")
        else:
            flags.append(f"yield~ (p={p:.3f})")
    # Calibration.
    if r["brier_skill"] is not None:
        if r["brier_skill"] > 0.02:
            flags.append("calibrated")
        elif r["brier_skill"] < -0.05:
            flags.append("MISCALIBRATED")
    return " | ".join(flags)


# ─── reporting ────────────────────────────────────────────────────────────────

def _pct(x, dp=1):
    return f"{x*100:+.{dp}f}%" if x is not None else "n/a"


def print_report(results: list[dict], min_n: int, live_only: bool):
    scope = "LIVE only" if live_only else "all (paper + live)"
    print(f"\n{'='*78}")
    print(f"  NOPREDICTIONS — EDGE EVALUATION   [{scope}]")
    print(f"{'='*78}")

    for r in results:
        print(f"\n┌─ [{r['id']}] {r['name']}")
        print(f"│  record   {r['won']}W / {r['lost']}L / {r['void']}V"
              f"   (n={r['n_settled']}, win rate "
              f"{_pct(r['win_rate']) if r['win_rate'] is not None else 'n/a'})")
        ci = r["yield_ci"]
        ci_s = f"[{_pct(ci[0])}, {_pct(ci[1])}]" if ci[0] is not None else "n/a"
        pz = r["p_yield_le_zero"]
        print(f"│  profit   staked {r['staked']:.1f}u  P&L {r['pnl']:+.2f}u  "
              f"yield {_pct(r['yield'])}")
        print(f"│           95% CI {ci_s}   p(yield>0)="
              f"{(1-pz):.3f}" if pz is not None else "│           CI n/a")
        if r["brier"] is not None:
            sk = r["brier_skill"]
            print(f"│  calib    Brier {r['brier']:.4f}  (base {r['brier_base']:.4f}, "
                  f"skill {sk:+.3f})" if sk is not None else
                  f"│  calib    Brier {r['brier']:.4f}")
            for b in r["reliability"]:
                bar_pred = "▏" * round(b["pred"] * 20)
                gap = b["actual"] - b["pred"]
                print(f"│             {b['bin']}  n={b['n']:>3}  "
                      f"pred {b['pred']:.2f}  actual {b['actual']:.2f}  "
                      f"({gap:+.2f})")
        if r["clv"]:
            c = r["clv"]
            susp = f", {c['suspect_excluded']} suspect dropped" if c.get("suspect_excluded") else ""
            print(f"│  CLV*     n={c['n']}  mean {_pct(c['mean'],2)}  "
                  f"{_pct(c['pct_positive'],0)} positive  p={c['p_value']:.3f}{susp}")
        else:
            print(f"│  CLV*     no usable closing-line data "
                  f"({r['circular_clv_excluded']} circular excluded)")
        print(f"└─ VERDICT: {_verdict(r, min_n)}")

    print(f"\n{'─'*78}")
    print("  * CLV uses only real sharp/pinnacle closing lines. Circular 'model CLV'")
    print("    (closing line == our own model_probability) is excluded — it is not edge.")
    print(f"{'─'*78}\n")


def main():
    ap = argparse.ArgumentParser(description="Edge-measurement harness")
    ap.add_argument("--strategy", type=int, default=None, help="single strategy id")
    ap.add_argument("--live", action="store_true", help="only pm_live=TRUE trades")
    ap.add_argument("--min-n", type=int, default=30, help="min settled for a real verdict")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    trades = _load(args.strategy, args.live)
    if not trades:
        print("No settled trades match the filter.")
        return

    by_strat: dict[int, list[dict]] = {}
    for t in trades:
        by_strat.setdefault(t["strategy_id"], []).append(t)

    results = [evaluate_strategy(ts) for ts in by_strat.values()]
    results.sort(key=lambda r: r["id"])

    if args.json:
        for r in results:
            r["verdict"] = _verdict(r, args.min_n)
        print(json.dumps(results, indent=2))
    else:
        print_report(results, args.min_n, args.live)


if __name__ == "__main__":
    main()
