#!/usr/bin/env python3
"""
World Cup pricer — our own line for national teams.

Turns a national-team Elo gap into Poisson goal expectations and prices the
markets the WC agent bets (1X2 + Over/Under) via the existing Monte Carlo
engine in agent/sim/. No sharp-book reference anywhere.

  Elo gap --(calibration: beta, baseline total)--> (lambda_h, lambda_a)
          --> agent/sim/ Monte Carlo --> 1X2 + O/U probabilities

CLI spot-check (sim vs closed-form Poisson, like sim_demo):
  cd agent && source ../ingest/.venv/bin/activate
  python wc_pricer.py --home Argentina --away "South Korea"
  python wc_pricer.py --home "United States" --away Wales --host "United States"
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

import national_elo  # noqa: E402  (RATINGS_PATH, load_ratings)
from sim.pricer import price_markets  # noqa: E402
from sim.simulator import SimConfig, simulate  # noqa: E402

LAMBDA_MIN = 0.15
LAMBDA_MAX = 5.0
OVER_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


# ── Elo → lambdas ─────────────────────────────────────────────────────

def lambdas(
    home: str,
    away: str,
    data: dict,
    neutral: bool = True,
    host: Optional[str] = None,
    home_adv: Optional[float] = None,
) -> Tuple[float, float]:
    """
    Effective Elo gap -> (lambda_home, lambda_away).

    WC matches are neutral by default. `host` lets a host nation (USA / MEX /
    CAN in 2026) carry home advantage when it plays at a home venue.
    """
    cal = data["calibration"]
    beta = cal["beta_goals_per_elo"]
    mu = cal.get("mu_total_finals", cal["mu_total"])      # WC == finals baseline
    ha = data["params"]["home_adv"] if home_adv is None else home_adv

    rh = data["ratings"][home]["rating"]
    ra = data["ratings"][away]["rating"]

    adv = ha if not neutral else 0.0
    if host is not None:
        if host == home:
            adv += ha
        elif host == away:
            adv -= ha

    supremacy = beta * ((rh + adv) - ra)
    lh = min(max((mu + supremacy) / 2.0, LAMBDA_MIN), LAMBDA_MAX)
    la = min(max((mu - supremacy) / 2.0, LAMBDA_MIN), LAMBDA_MAX)
    return lh, la


# ── Closed-form Poisson (ground-truth sanity, mirrors sim/calibrator) ──

def analytical_markets(lh: float, la: float, max_goals: int = 12) -> Dict[str, float]:
    ph = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(max_goals + 1)]
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(max_goals + 1)]
    home_win = draw = away_win = 0.0
    over = {ln: 0.0 for ln in OVER_LINES}
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = ph[i] * pa[j]
            if i > j:
                home_win += p
            elif i == j:
                draw += p
            else:
                away_win += p
            for ln in OVER_LINES:
                if i + j > ln:
                    over[ln] += p
    out: Dict[str, float] = {
        "home_win": home_win, "draw": draw, "away_win": away_win,
        "exp_total_goals": lh + la,
    }
    for ln in OVER_LINES:
        key = f"over_{str(ln).replace('.', '_')}"      # over_2_5
        out[key] = over[ln]
        out[key.replace("over", "under")] = 1.0 - over[ln]
    return out


# ── Market pricer ─────────────────────────────────────────────────────

def price(
    home: str,
    away: str,
    data: dict,
    neutral: bool = True,
    host: Optional[str] = None,
    method: str = "sim",
    n_sims: int = 50_000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Price 1X2 + O/U for a fixture. method='sim' (default) or 'poisson'."""
    lh, la = lambdas(home, away, data, neutral=neutral, host=host)
    if method == "poisson":
        mk: Dict[str, Any] = dict(analytical_markets(lh, la))
    else:
        res = simulate(lh, la, config=SimConfig(n_sims=n_sims, seed=seed))
        mk = price_markets(res)
    mk["lambda_home"] = lh
    mk["lambda_away"] = la
    return mk


# ── Name resolution (CLI convenience) ─────────────────────────────────

def _db_canonical(name: str) -> Optional[str]:
    """Resolve a free-text nation to its DB canonical name via aliases."""
    try:
        from tools.db import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT canonical_name FROM teams "
                "WHERE LOWER(canonical_name) = LOWER(%s) LIMIT 1", (name,))
            r = cur.fetchone()
            if r:
                return r[0]
            cur.execute(
                "SELECT t.canonical_name FROM team_aliases a "
                "JOIN teams t ON t.id = a.team_id "
                "WHERE LOWER(a.alias) = LOWER(%s) LIMIT 1", (name,))
            r = cur.fetchone()
            if r:
                return r[0]
    except Exception:
        return None
    return None


def resolve_team(name: str, data: dict, use_db: bool = True) -> Optional[str]:
    """Map a free-text nation to a ratings key (exact -> ci -> alias -> substr)."""
    ratings = data["ratings"]
    if name in ratings:
        return name
    low = {k.lower(): k for k in ratings}
    if name.lower() in low:
        return low[name.lower()]
    if use_db:
        canon = _db_canonical(name)
        if canon and canon in ratings:
            return canon
    hits = [k for k in ratings if name.lower() in k.lower() or k.lower() in name.lower()]
    return hits[0] if len(hits) == 1 else None


# ── CLI ───────────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{x * 100:6.2f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="World Cup pricer (Elo -> sim)")
    ap.add_argument("--home", required=True)
    ap.add_argument("--away", required=True)
    ap.add_argument("--neutral", dest="neutral", action="store_true", default=True)
    ap.add_argument("--no-neutral", dest="neutral", action="store_false")
    ap.add_argument("--host", default=None, help="host nation (carries home adv)")
    ap.add_argument("--n-sims", type=int, default=50_000)
    args = ap.parse_args()

    if not national_elo.RATINGS_PATH.exists():
        print("No ratings json — run: python national_elo.py --build")
        return 1
    data = national_elo.load_ratings()

    home = resolve_team(args.home, data)
    away = resolve_team(args.away, data)
    if not home or not away:
        miss = args.home if not home else args.away
        print(f"Could not resolve '{miss}' to a rated nation.")
        return 1

    host = resolve_team(args.host, data) if args.host else None
    lh, la = lambdas(home, away, data, neutral=args.neutral, host=host)

    sim_p = price(home, away, data, neutral=args.neutral, host=host,
                  method="sim", n_sims=args.n_sims)
    poi_p = price(home, away, data, neutral=args.neutral, host=host,
                  method="poisson")

    rh = data["ratings"][home]["rating"]
    ra = data["ratings"][away]["rating"]
    print(f"\n  {home} ({rh:.0f})  vs  {away} ({ra:.0f})"
          f"{'   [neutral]' if args.neutral and not host else ''}"
          f"{('   host=' + host) if host else ''}")
    print(f"  lambdas:  λ_home = {lh:.3f}    λ_away = {la:.3f}\n")
    print(f"  {'Market':<12}{'Sim':>10}{'Poisson':>10}{'Δpp':>9}")
    print("  " + "-" * 41)
    for mkt in ("home_win", "draw", "away_win",
                "over_1_5", "over_2_5", "over_3_5"):
        s = sim_p.get(mkt, float("nan"))
        p = poi_p.get(mkt, float("nan"))
        print(f"  {mkt:<12}{_pct(s):>10}{_pct(p):>10}{(s - p) * 100:+8.2f}")
    print(f"\n  exp total goals (sim): {sim_p['exp_total_goals']:.2f}")
    print("  (sim − poisson deltas are the state-dynamics effect: draws/overs"
          " drift up slightly — expected, see sim/calibrator.py)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
