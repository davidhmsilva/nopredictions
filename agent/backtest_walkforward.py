#!/usr/bin/env python3
"""
backtest_walkforward.py — no-lookahead walk-forward backtest of the DC model.

For each test period: retrain DC ONLY on matches before the period (optionally a
trailing window), then for every match in the period compare the DC fair prob to
the de-vigged Pinnacle OPENING price and "bet" each outcome whose edge ≥ threshold.
Settle at the opening odds vs the real result (yield) and compare the opening odds
to the Pinnacle CLOSING odds (CLV). This is the project's gold-standard test:
positive yield with flat/negative CLV = luck; consistent +CLV = real edge.

Usage:
  python backtest_walkforward.py                      # full run 2018+, semiannual
  python backtest_walkforward.py --start 2024-01-01   # quick smoke test
  python backtest_walkforward.py --threshold 0.05 --retrain-months 3 --train-window-years 4
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
import numpy as np
import psycopg2, psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), "../ingest/.env"))
from dixon_coles import DixonColesModel
from dc_trainer import load_training_data, vig_remove
from dc_scanner import _norm

DB = os.getenv("DATABASE_URL")


def load_odds_matches(min_date: str):
    """Matches with Pinnacle opening (PS) + closing (PSC) + result."""
    sql = """
      SELECT m.kickoff_utc, ht.canonical_name home, at.canonical_name away,
             m.home_score hs, m.away_score as_,
             o1.home_odds oh, o1.draw_odds od, o1.away_odds oa,
             c1.home_odds ch, c1.draw_odds cd, c1.away_odds ca
      FROM matches m
      JOIN teams ht ON ht.id=m.home_team_id JOIN teams at ON at.id=m.away_team_id
      JOIN match_odds o1 ON o1.match_id=m.id JOIN bookmakers b1 ON b1.id=o1.bookmaker_id AND b1.code='PS'
      JOIN match_odds c1 ON c1.match_id=m.id JOIN bookmakers b2 ON b2.id=c1.bookmaker_id AND b2.code='PSC'
      WHERE m.status='finished' AND m.home_score IS NOT NULL
        AND o1.home_odds IS NOT NULL AND o1.draw_odds IS NOT NULL AND o1.away_odds IS NOT NULL
        AND c1.home_odds IS NOT NULL AND c1.draw_odds IS NOT NULL AND c1.away_odds IS NOT NULL
        AND m.kickoff_utc >= %s
      ORDER BY m.kickoff_utc
    """
    c = psycopg2.connect(DB); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, (min_date,)); rows = cur.fetchall(); c.close()
    out = []
    for r in rows:
        ko = r["kickoff_utc"]
        if ko.tzinfo is None: ko = ko.replace(tzinfo=timezone.utc)
        res = "home" if r["hs"] > r["as_"] else ("away" if r["hs"] < r["as_"] else "draw")
        out.append({"ko": ko, "home": r["home"], "away": r["away"], "result": res,
                    "open": (float(r["oh"]), float(r["od"]), float(r["oa"])),
                    "close": (float(r["ch"]), float(r["cd"]), float(r["ca"]))})
    return out


def add_months(d: datetime, n: int) -> datetime:
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


def boot_ci(x, n=10000):
    x = np.asarray(x, float)
    if len(x) < 2: return (float("nan"), float("nan"))
    idx = np.random.randint(0, len(x), (n, len(x)))
    means = x[idx].mean(1)
    return tuple(np.percentile(means, [2.5, 97.5]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01", help="first test date")
    ap.add_argument("--min-date", default="2012-01-01", help="earliest training data")
    ap.add_argument("--retrain-months", type=int, default=6)
    ap.add_argument("--train-window-years", type=float, default=4.0, help="trailing training window (0=all)")
    ap.add_argument("--threshold", type=float, default=0.03, help="min edge (prob) to bet")
    ap.add_argument("--l2-reg", type=float, default=1.0)
    args = ap.parse_args()
    np.random.seed(0)

    print(f"Loading data...", flush=True)
    train_all = load_training_data(min_date=args.min_date)
    for m in train_all:
        if m["kickoff_utc"].tzinfo is None:
            m["kickoff_utc"] = m["kickoff_utc"].replace(tzinfo=timezone.utc)
    test_all = load_odds_matches(args.start)
    print(f"  train pool {len(train_all):,} | test (with Pinnacle open+close) {len(test_all):,}\n", flush=True)

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = max(m["ko"] for m in test_all)
    win_days = args.train_window_years * 365.25

    bets = []   # each: dict(edge, entry_odds, close_odds, won, p, q, dt)
    p0 = start
    while p0 < end:
        p1 = add_months(p0, args.retrain_months)
        lo = p0.timestamp() - win_days * 86400 if win_days else 0
        train = [m for m in train_all if lo <= m["kickoff_utc"].timestamp() < p0.timestamp()]
        period_test = [t for t in test_all if p0 <= t["ko"] < p1]
        if len(train) < 500 or not period_test:
            p0 = p1; continue
        model = DixonColesModel(l2_reg=args.l2_reg); model.fit(train, ref_date=p0)
        if not getattr(model, "fit_success", True):
            print(f"  {p0:%Y-%m} WARN non-converged fit, skipping period", flush=True); p0 = p1; continue
        idx = {_norm(t): i for i, t in enumerate(model.teams)}
        n_bet = 0
        for t in period_test:
            hi, ai = idx.get(_norm(t["home"])), idx.get(_norm(t["away"]))
            if hi is None or ai is None: continue
            pred = model.predict(model.teams[hi], model.teams[ai])
            P = (pred["home_win"], pred["draw"], pred["away_win"])
            q = vig_remove(*t["open"])
            for k, oc in enumerate(("home", "draw", "away")):
                edge = P[k] - q[k]
                if edge >= args.threshold:
                    eo, co = t["open"][k], t["close"][k]
                    bets.append({"edge": edge, "entry": eo, "close": co,
                                 "won": t["result"] == oc, "p": P[k], "q": q[k]})
                    n_bet += 1
        print(f"  {p0:%Y-%m}: trained {len(train):5} | tested {len(period_test):5} | bets {n_bet}", flush=True)
        p0 = p1

    # ---- aggregate ----
    if not bets:
        print("\nNo bets."); return
    pnl = np.array([(b["entry"] - 1) if b["won"] else -1.0 for b in bets])
    clv = np.array([b["entry"] / b["close"] - 1 for b in bets])
    won = np.array([b["won"] for b in bets])
    n = len(bets)
    yci = boot_ci(pnl); cci = boot_ci(clv)
    print("\n" + "=" * 74)
    print(f"WALK-FORWARD BACKTEST — DC vs Pinnacle (open entry, close benchmark)")
    print(f"  threshold {args.threshold*100:.0f}pp | retrain {args.retrain_months}mo | window {args.train_window_years}y")
    print("=" * 74)
    print(f"  bets: {n:,}   win rate: {won.mean()*100:.1f}%")
    print(f"  YIELD: {pnl.mean()*100:+.2f}%   95% CI [{yci[0]*100:+.2f}%, {yci[1]*100:+.2f}%]")
    print(f"  CLV:   {clv.mean()*100:+.2f}%   95% CI [{cci[0]*100:+.2f}%, {cci[1]*100:+.2f}%]   (+ = beat the close)")
    print(f"  → {'CLV+ (sig)' if cci[0]>0 else ('CLV− (sig)' if cci[1]<0 else 'CLV ~ (CI spans 0)')}"
          f" | {'YIELD+ (sig)' if yci[0]>0 else ('YIELD− (sig)' if yci[1]<0 else 'YIELD ~ (CI spans 0)')}")
    print("\n  by edge bucket:")
    edges = np.array([b["edge"] for b in bets])
    for lo, hi in [(0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.0)]:
        msk = (edges >= lo) & (edges < hi)
        if msk.sum() == 0: continue
        print(f"    edge {lo*100:.0f}-{hi*100:.0f}pp: n={msk.sum():5}  yield {pnl[msk].mean()*100:+6.1f}%  "
              f"CLV {clv[msk].mean()*100:+5.2f}%  win {won[msk].mean()*100:.0f}%")


if __name__ == "__main__":
    main()
