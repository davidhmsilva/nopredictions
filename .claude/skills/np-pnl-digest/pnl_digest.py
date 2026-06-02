#!/usr/bin/env python3
"""np-pnl-digest — weekly NOPREDICTIONS P&L digest (markdown) for site/Twitter.

Honest by construction: separates live (real money) from paper, reports real
Pinnacle CLV only (circular model-CLV excluded), and never oversells.

Usage:  python pnl_digest.py [--days 7]
"""
import argparse, os, sys
import psycopg2, psycopg2.extras
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
load_dotenv(os.path.join(REPO, "ingest", ".env"))

ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=7); a = ap.parse_args()
c = psycopg2.connect(os.getenv("DATABASE_URL")); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def block(where, params):
    cur.execute(f"""
      SELECT COUNT(*) n,
             COUNT(*) FILTER (WHERE result='won') w,
             COUNT(*) FILTER (WHERE result='lost') l,
             COUNT(*) FILTER (WHERE result='void') v,
             COALESCE(SUM(stake_units),0) staked,
             COALESCE(SUM(payout_units-stake_units),0) pnl
      FROM paper_trades WHERE result IN ('won','lost','void') AND {where}""", params)
    return cur.fetchone()

since = f"resolved_at > NOW() - INTERVAL '{a.days} days'"
allr = block(since, [])
live = block(f"{since} AND pm_live=TRUE", [])
paper_pnl = float(allr['pnl']) - float(live['pnl'])

def yld(r):
    s=float(r['staked']); return (float(r['pnl'])/s*100) if s else 0.0

print(f"# NOPREDICTIONS — last {a.days} days\n")
print(f"**Settled:** {allr['n']}  ({allr['w']}W / {allr['l']}L / {allr['v']}V)")
print(f"**Net P&L:** {float(allr['pnl']):+.2f}u on {float(allr['staked']):.0f}u staked  ({yld(allr):+.1f}% yield)")
print(f"- Real money (live): {float(live['pnl']):+.2f}u over {live['n']} settled ({yld(live):+.1f}% yield)")
print(f"- Paper: {paper_pnl:+.2f}u\n")

print("## By strategy")
cur.execute(f"""
  SELECT s.name, COUNT(*) n,
         COUNT(*) FILTER (WHERE pt.result='won') w,
         SUM(pt.payout_units-pt.stake_units) pnl, SUM(pt.stake_units) st,
         COUNT(*) FILTER (WHERE pt.clv_source IN ('sharp_closing','pinnacle_fd')) clvn,
         ROUND(AVG(pt.clv) FILTER (WHERE pt.clv_source IN ('sharp_closing','pinnacle_fd'))::numeric,4) avgclv
  FROM paper_trades pt JOIN strategies s ON s.id=pt.strategy_id
  WHERE pt.result IN ('won','lost','void') AND pt.{since}
  GROUP BY s.name ORDER BY pnl DESC NULLS LAST""", [])
print(f"| strategy | n | W | P&L | yield | real-CLV (n) |")
print(f"|---|---|---|---|---|---|")
for r in cur.fetchall():
    st=float(r['st'] or 0); y=(float(r['pnl'] or 0)/st*100) if st else 0
    clv=f"{float(r['avgclv'])*100:+.1f}% ({r['clvn']})" if r['avgclv'] is not None else f"— ({r['clvn']})"
    print(f"| {r['name']} | {r['n']} | {r['w']} | {float(r['pnl'] or 0):+.2f}u | {y:+.1f}% | {clv} |")

print("\n## What Polymarket got wrong / right (biggest settled)")
cur.execute(f"""
  SELECT pm.title, pt.outcome, pt.result, (pt.payout_units-pt.stake_units) pnl, pt.entry_odds
  FROM paper_trades pt LEFT JOIN pm_markets pm ON pm.id=pt.market_id
  WHERE pt.result IN ('won','lost') AND pt.{since}
  ORDER BY ABS(pt.payout_units-pt.stake_units) DESC LIMIT 5""", [])
for r in cur.fetchall():
    mark = "✅" if r['result']=='won' else "❌"
    print(f"- {mark} {float(r['pnl']):+.2f}u — {(r['title'] or '?')[:50]} [{r['outcome']}] @{float(r['entry_odds'] or 0):.2f}")

print("\n> CLV is king: real Pinnacle CLV shown only where a sharp closing line exists "
      "(~20% of trades). Positive yield without positive CLV is luck. Edge is unproven "
      "until CLV is consistently positive over 200+ selections.")
c.close()
