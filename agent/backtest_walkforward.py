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
             m.home_score hs, m.away_score as_, l.code league,
             o1.home_odds oh, o1.draw_odds od, o1.away_odds oa,
             c1.home_odds ch, c1.draw_odds cd, c1.away_odds ca
      FROM matches m
      JOIN teams ht ON ht.id=m.home_team_id JOIN teams at ON at.id=m.away_team_id
      JOIN seasons s ON s.id=m.season_id JOIN leagues l ON l.id=s.league_id
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
        out.append({"ko": ko, "home": r["home"], "away": r["away"], "result": res, "league": r["league"],
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
        n_priced = 0
        for t in period_test:
            hi, ai = idx.get(_norm(t["home"])), idx.get(_norm(t["away"]))
            if hi is None or ai is None: continue
            pred = model.predict(model.teams[hi], model.teams[ai])
            P = (pred["home_win"], pred["draw"], pred["away_win"])
            q = vig_remove(*t["open"])
            n_priced += 1
            for k, oc in enumerate(("home", "draw", "away")):
                bets.append({"edge": P[k] - q[k], "entry": t["open"][k], "close": t["close"][k],
                             "won": t["result"] == oc, "league": t["league"]})
        print(f"  {p0:%Y-%m}: trained {len(train):5} | tested {len(period_test):5} | priced {n_priced}", flush=True)
        p0 = p1

    # ---- aggregate (slice the recorded outcomes many ways) ----
    if not bets:
        print("\nNo priceable matches."); return

    def stats(sub):
        pnl = np.array([(b["entry"] - 1) if b["won"] else -1.0 for b in sub])
        clv = np.array([b["entry"] / b["close"] - 1 for b in sub])
        won = np.array([b["won"] for b in sub], float)
        return dict(n=len(sub), win=won.mean(), yld=pnl.mean(), clv=clv.mean(),
                    yci=boot_ci(pnl), cci=boot_ci(clv))

    def verdict(s):
        c = "CLV+ (sig)" if s["cci"][0] > 0 else ("CLV− (sig)" if s["cci"][1] < 0 else "CLV~ (spans 0)")
        return c

    thr = args.threshold
    print("\n" + "=" * 80)
    print(f"WALK-FORWARD BACKTEST — DC vs Pinnacle | threshold {thr*100:.0f}pp | "
          f"retrain {args.retrain_months}mo | window {args.train_window_years}y")
    print("=" * 80)
    for label, sub in [("NORMAL  — back outcomes DC says are underpriced (edge ≥ +thr)",
                        [b for b in bets if b["edge"] >= thr]),
                       ("FADE    — back outcomes DC says are overpriced  (edge ≤ −thr)",
                        [b for b in bets if b["edge"] <= -thr])]:
        if not sub:
            print(f"\n{label}\n  no bets"); continue
        s = stats(sub)
        print(f"\n{label}")
        print(f"  n={s['n']:,}  win {s['win']*100:.1f}%  "
              f"YIELD {s['yld']*100:+.2f}% [{s['yci'][0]*100:+.1f},{s['yci'][1]*100:+.1f}]  "
              f"CLV {s['clv']*100:+.2f}% [{s['cci'][0]*100:+.2f},{s['cci'][1]*100:+.2f}]  → {verdict(s)}")

    # By league (normal strategy) — hunt for a +CLV niche where Pinnacle is less sharp
    from collections import defaultdict
    byl = defaultdict(list)
    for b in bets:
        if b["edge"] >= thr: byl[b["league"]].append(b)
    rows = [(lg, stats(sub)) for lg, sub in byl.items() if len(sub) >= 300]
    print(f"\nBY LEAGUE (normal strategy, leagues with ≥300 bets, sorted by CLV desc):")
    print(f"  {'league':14}{'n':>6}{'yield':>9}{'CLV':>9}{'CLV 95% CI':>22}")
    for lg, s in sorted(rows, key=lambda x: -x[1]["clv"]):
        flag = "  <== +CLV (CI>0)" if s["cci"][0] > 0 else ""
        print(f"  {(lg or '?'):14}{s['n']:>6}{s['yld']*100:>+8.1f}%{s['clv']*100:>+8.2f}%"
              f"   [{s['cci'][0]*100:+.2f},{s['cci'][1]*100:+.2f}]{flag}")


if __name__ == "__main__":
    main()
