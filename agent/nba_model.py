"""
nba_model.py — price any NBA spread / total / moneyline line from the SHARP main line.

The NBA twin of nfl_model.py, and the same idea: not a forecast. Pinnacle says
what a game is worth; Polymarket lists ~25 alternate spreads and ~20 alternate
totals per game on half-points, and this file translates the one into the other.

What is different from the NFL, and why it matters here:
  * **No ties.** Overtime ends every tied game, so a final margin of 0 is
    impossible and carries no mass. The moneyline is P(margin > 0), with no
    50-50 rule to price.
  * **No key numbers worth the name.** Margins 1-2 are rarer than a normal
    says (end-game fouling stretches close games), 5-8 slightly commoner. The
    same IPF factor fit learns that shape; nothing is hard-coded.
  * **The dispersion moved.** σ of (margin − closing expectation) went
    11.8 (2014-15) → 12.3 (2017-18) → 13.7 (2021-22). So the factors are fitted
    on 2018-19 onwards only, and per game σ is solved from Pinnacle's own
    spread + moneyline, exactly as for the NFL. The historical σ is only a
    fallback near pick'em, where the moneyline cannot identify it.
  * **Totals are ~220, not ~45.** σ_T ≈ 18.3 on the same seasons.

Data: `bt_nba` (consensus closing spread + total + result, 2014-15 → 2021-22,
Stage H), cached to ingest/.cache/nba/lines.csv on first use. Consensus close,
not Pinnacle — the fit uses the close only as each game's expected margin,
where the two agree to well under a point.

    python nba_model.py --validate     # history + consistency checks
"""
from __future__ import annotations

import argparse
import os
from functools import lru_cache

import numpy as np
import pandas as pd

from nfl_model import Anchor, KeyedDist, _fit, _solve

LINES_CSV = os.path.join(os.path.dirname(__file__), "../ingest/.cache/nba/lines.csv")

MIN_SEASON = 2018          # season_start; σ was ~1.5 points tighter before (see above)
MARGIN_MAX = 80            # the largest NBA margin on record is 73
TOTAL_LO, TOTAL_HI = 100, 360
SIGMA_LO, SIGMA_HI = 10.0, 17.0
SIGMA_SOLVE_MIN_MU = 1.5   # below this |μ| the moneyline cannot identify σ


def load_games(path: str = LINES_CSV) -> pd.DataFrame:
    """Closing lines + results. Pulled from `bt_nba` once, then read from disk:
    the agent runs every 5 minutes and must not re-query 10k rows each time."""
    if not os.path.exists(path):
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "../ingest/.env"))
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT season_start, is_playoff, margin, total_points,
                                      close_spread_home, close_total, close_ml_home, close_ml_away
                                 FROM bt_nba
                                WHERE close_spread_home IS NOT NULL AND close_total IS NOT NULL""")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
        finally:
            conn.close()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    g = pd.read_csv(path)
    g = g[g["season_start"] >= MIN_SEASON].copy()
    # close_spread_home is the home HANDICAP (−6 = home laying 6); μ is its negative
    g["mu"] = -g["close_spread_home"].astype(float)
    return g


@lru_cache(maxsize=1)
def margin_dist() -> KeyedDist:
    g = load_games()
    s = np.concatenate([g["mu"].values, -g["mu"].values]).astype(float)
    r = np.concatenate([g["margin"].values, -g["margin"].values]).astype(float)
    return _fit(r, s, np.arange(-MARGIN_MAX, MARGIN_MAX + 1), symmetric=True, impossible=(0,))


@lru_cache(maxsize=1)
def total_dist() -> KeyedDist:
    g = load_games()
    return _fit(g["total_points"].values.astype(float), g["close_total"].values.astype(float),
                np.arange(TOTAL_LO, TOTAL_HI + 1), symmetric=False)


# ── pricing ──────────────────────────────────────────────────────────────────

def win_prob(mu: float, sigma: float | None = None) -> float:
    """P(team wins). A margin of 0 has no mass, so there is no tie to split."""
    return margin_dist().p_over(mu, 0.0, sigma)


def cover_prob(mu: float, give: float, sigma: float | None = None) -> float:
    """Token value of 'team -give' (give > 0 = laying points; a +3.5 dog is
    give = -3.5): 1 when margin > give, 0.5 on exactly give."""
    return margin_dist().p_over(mu, give, sigma)


def over_prob(T: float, line: float) -> float:
    return total_dist().p_over(T, line)


def mu_from_spread(give: float, p_cover: float, sigma: float | None = None) -> float:
    d = margin_dist()
    return _solve(lambda m: d.p_over_no_push(m, give, sigma), p_cover, -50, 50)


def mu_from_ml(p_win: float, sigma: float | None = None) -> float:
    return _solve(lambda m: win_prob(m, sigma), p_win, -50, 50)


def T_from_total(line: float, p_over: float) -> float:
    d = total_dist()
    return _solve(lambda t: d.p_over_no_push(t, line), p_over, TOTAL_LO + 20, TOTAL_HI - 20)


def anchor(*, give_a: float | None, p_cover_a: float | None, p_win_a: float | None,
           total_line: float | None, p_over: float | None) -> Anchor:
    """give_a: team A's handicap as points laid (bookmaker 'A -6.0' → 6.0,
    'A +3.0' → -3.0). Probabilities are de-vigged, for team A / the over.
    Same procedure as nfl_model.anchor: μ and σ solved together from the
    spread and the moneyline, the moneyline residual kept as a health check."""
    d = margin_dist()
    sigma, solved, mu = d.sigma, False, None
    if give_a is not None and p_cover_a is not None:
        mu = mu_from_spread(give_a, p_cover_a)
        if p_win_a is not None and abs(mu) >= SIGMA_SOLVE_MIN_MU:
            best = None
            for s in np.arange(SIGMA_LO, SIGMA_HI + 1e-9, 0.25):
                m = mu_from_spread(give_a, p_cover_a, float(s))
                err = abs(win_prob(m, float(s)) - p_win_a)
                if best is None or err < best[0]:
                    best = (err, float(s), m)
            _, sigma, mu = best
            solved = True
    elif p_win_a is not None:
        mu = mu_from_ml(p_win_a)
    resid = 100.0 * (win_prob(mu, sigma) - p_win_a) if (mu is not None and p_win_a is not None) else 0.0
    T = T_from_total(total_line, p_over) if total_line is not None and p_over is not None else None
    return Anchor(mu=mu, sigma=sigma, T=T, ml_resid_pp=resid, sigma_solved=solved)


# ── validation ───────────────────────────────────────────────────────────────

def validate() -> None:
    g = load_games()
    d, td = margin_dist(), total_dist()
    print(f"games {len(g)} (seasons {g.season_start.min()}-{g.season_start.max()})  "
          f"margin σ={d.sigma:.2f}  total σ={td.sigma:.2f}")
    sup = d.support
    print("  margin factors: " + "  ".join(f"m({k})={d.factor[sup == k][0]:.2f}"
                                           for k in (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15)))

    print("\nP(home margin > x) by closing expectation μ — model/empirical (Δ pp):")
    for lo, hi in ((-1.0, 1.0), (2.0, 4.0), (5.0, 7.5), (8.0, 11.0), (11.5, 16.0)):
        sub = g[(g.mu >= lo) & (g.mu <= hi)]
        row = []
        for off in (-10.5, -5.5, -2.5, 2.5, 5.5, 10.5):
            emp = float(np.mean([m > mu + off for m, mu in zip(sub.margin, sub.mu)]))
            mod = float(np.mean([d.p_over(float(mu), float(mu) + off) for mu in sub.mu]))
            row.append(f"{off:+.1f}: {mod:.3f}/{emp:.3f} ({100 * (mod - emp):+.1f})")
        print(f"  μ∈[{lo},{hi}] n={len(sub):>4}  " + "  ".join(row))

    print("\nP(total > line + offset) — model/empirical (Δ pp):")
    for lo, hi in ((195, 212), (212, 222), (222, 235)):
        sub = g[(g.close_total >= lo) & (g.close_total < hi)]
        row = []
        for off in (-12.5, -6.5, -2.5, 2.5, 6.5, 12.5):
            emp = float((sub.total_points > sub.close_total + off).mean())
            mod = float(np.mean([td.p_over(float(T), float(T) + off) for T in sub.close_total]))
            row.append(f"{off:+.1f}: {mod:.3f}/{emp:.3f} ({100 * (mod - emp):+.1f})")
        print(f"  T∈[{lo},{hi}) n={len(sub):>4}  " + "  ".join(row))

    # the consensus moneyline against the spread-implied one, at the historical σ:
    # how much a per-game σ has to do
    ml = g.dropna(subset=["close_ml_home", "close_ml_away"])
    x, y = 1 / ml.close_ml_home.astype(float), 1 / ml.close_ml_away.astype(float)
    ml = ml.assign(p_book=x / (x + y))
    print("\nbook ML vs model ML at the historical σ, by |μ| (book − model, pp):")
    for lo, hi in ((1.5, 4), (4, 7), (7, 10), (10, 14)):
        sub = ml[(ml.mu.abs() >= lo) & (ml.mu.abs() < hi)]
        gap = [100 * (pb - win_prob(float(mu))) * (1 if mu > 0 else -1)
               for pb, mu in zip(sub.p_book, sub.mu)]
        print(f"  |μ|∈[{lo},{hi}) n={len(sub):>4}  favourite: book − model {np.mean(gap):+.2f}pp")


if __name__ == "__main__":
    argparse.ArgumentParser().add_argument("--validate", action="store_true")
    validate()
