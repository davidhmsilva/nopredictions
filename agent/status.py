"""
status.py — one-glance report of what the agent is doing.

  cd agent && source ../ingest/.venv/bin/activate && python status.py

Shows: P&L + bankroll, last scans and what they found, last settled entries,
next (open) entries by kickoff, in-play activity, and the observation/shadow layer.
Read-only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

try:
    from .tools.db import run_analysis_query as Q
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
    from db import run_analysis_query as Q  # type: ignore

LIVE_MODE = os.getenv("PM_LIVE_MODE", "0") == "1"


def _ago(ts) -> str:
    if ts is None:
        return "never"
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    now = datetime.now(timezone.utc)
    secs = (now - ts).total_seconds()
    if secs < 0:
        m = int(-secs // 60)
        return f"in {m//60}h{m%60:02d}m" if m >= 60 else f"in {m}m"
    h = secs / 3600
    if h < 1:
        return f"{int(secs//60)}m ago"
    if h < 48:
        return f"{h:.1f}h ago"
    return f"{int(h//24)}d ago"


def _hr(t):
    print("─" * 72)


def section(title):
    print(f"\n{title}")


def pnl_block():
    section("💰  P&L & BANKROLL")
    r = Q("""
        SELECT
          COUNT(*) FILTER (WHERE result IS NOT NULL) settled,
          COUNT(*) FILTER (WHERE result='won') w,
          COUNT(*) FILTER (WHERE result='lost') l,
          COUNT(*) FILTER (WHERE result='void') v,
          ROUND(SUM(payout_units - stake_units) FILTER (WHERE result IS NOT NULL),2) pnl,
          ROUND(SUM(stake_units) FILTER (WHERE result IS NOT NULL),2) staked
        FROM paper_trades WHERE strategy_id <> 9
    """)[0]
    yld = (float(r["pnl"]) / float(r["staked"]) * 100) if r["staked"] else 0
    print(f"  realized (all paper+live): {r['pnl']:+}u  on {r['staked']}u staked  "
          f"({yld:+.1f}% yield)")
    print(f"  record: {r['w']}W / {r['l']}L / {r['v']}V  of {r['settled']} settled")

    lv = Q("""
        SELECT
          ROUND(SUM(payout_units - stake_units) FILTER (WHERE result IS NOT NULL),2) pnl,
          COUNT(*) FILTER (WHERE result IS NOT NULL) settled,
          ROUND(SUM(pm_order_size*pm_order_price) FILTER (
              WHERE result IS NULL AND pm_order_status IN ('live','matched')),2) open_notional,
          COUNT(*) FILTER (WHERE result IS NULL AND pm_order_status IN ('live','matched')) open_n,
          ROUND(SUM(pm_cash_pnl) FILTER (WHERE result IS NULL),2) unrealized
        FROM paper_trades WHERE pm_live = TRUE
    """)[0]
    print(f"  LIVE (real money): realized {lv['pnl'] or 0:+}u over {lv['settled'] or 0} settled  | "
          f"open {lv['open_n'] or 0} pos, ${lv['open_notional'] or 0} notional  | "
          f"unrealized {lv['unrealized'] or 0:+} $ (mark-to-market)")


def scans_block():
    section("🔭  LAST SCANS (edge hunts)")
    rows = Q("""
        SELECT run_type, MAX(finished_at) last, MAX(output_summary) summ
        FROM agent_runs
        WHERE run_type IN ('dc_scanner:daily','sim_scanner:daily','nba_scanner:daily')
        GROUP BY run_type ORDER BY last DESC
    """)
    for r in rows:
        name = r["run_type"].split(":")[0]
        print(f"  {name:12} {_ago(r['last']):>10}  | {r['summ'] or ''}")
    ip = Q("""
        SELECT MAX(finished_at) last, MAX(output_summary) summ
        FROM agent_runs WHERE run_type='orchestrator:cycle'
    """)[0]
    print(f"  in-play      {_ago(ip['last']):>10}  | last cycle: {ip['summ'] or ''}")


def last_entries_block():
    section("✅  LAST ENTRIES (settled)")
    rows = Q("""
        SELECT pt.placed_at, pt.resolved_at, pt.outcome, pt.result, pt.entry_odds,
               ROUND(pt.payout_units - pt.stake_units,2) pl, pt.pm_live,
               s.name strat, pm.title
        FROM paper_trades pt JOIN strategies s ON s.id=pt.strategy_id
        LEFT JOIN pm_markets pm ON pm.id=pt.market_id
        WHERE pt.result IS NOT NULL AND pt.strategy_id <> 9
        ORDER BY pt.resolved_at DESC NULLS LAST LIMIT 8
    """)
    for r in rows:
        tag = {"won": "✔ WON ", "lost": "✗ LOST", "void": "• VOID"}.get(r["result"], r["result"])
        live = "💵" if r["pm_live"] else "  "
        title = (r["title"] or "")[:38]
        print(f"  {live}{tag} {str(r['pl']):>6}u  {r['outcome'][:16]:16} {title:38} "
              f"[{r['strat'][:10]}] {_ago(r['resolved_at'])}")


def next_entries_block():
    section("⏳  NEXT ENTRIES (open positions, by kickoff)")
    rows = Q("""
        SELECT pt.outcome, pt.entry_odds, pt.expected_edge, pt.pm_live, pt.pm_order_status,
               s.name strat, pm.title, pm.resolution_time
        FROM paper_trades pt JOIN strategies s ON s.id=pt.strategy_id
        LEFT JOIN pm_markets pm ON pm.id=pt.market_id
        WHERE pt.result IS NULL AND pt.strategy_id <> 9
          AND pm.resolution_time > NOW()
        ORDER BY pm.resolution_time ASC LIMIT 12
    """)
    if not rows:
        print("  (none upcoming)")
    for r in rows:
        live = "💵" if r["pm_live"] else "  "
        st = f"[{r['pm_order_status']}]" if r["pm_live"] else ""
        edge = f"+{float(r['expected_edge'])*100:.1f}pp" if r["expected_edge"] is not None else "?"
        title = (r["title"] or "")[:40]
        print(f"  {live}{_ago(r['resolution_time']):>8}  {r['outcome'][:15]:15} {edge:>7}  "
              f"{title:40} [{r['strat'][:9]}] {st}")
    n = Q("""SELECT COUNT(*) n FROM paper_trades pt LEFT JOIN pm_markets pm ON pm.id=pt.market_id
            WHERE pt.result IS NULL AND pt.strategy_id<>9 AND pm.resolution_time > NOW()""")[0]["n"]
    if n > 12:
        print(f"  … +{n-12} more open")


def shadow_block():
    section("🔬  OBSERVATION + SHADOW EDGE (no money)")
    r = Q("""
        SELECT COUNT(*) n, MAX(observed_at) last,
               COUNT(DISTINCT (home,away)) matches
        FROM market_observations
    """)[0]
    print(f"  observations: {r['n']} rows, {r['matches']} matches, last {_ago(r['last'])}")
    last_run = Q("""
        SELECT COUNT(*) n,
          COUNT(*) FILTER (WHERE edge_pp>=3) naive,
          COUNT(*) FILTER (WHERE refined_bet_ok) refined,
          COUNT(*) FILTER (WHERE refined_bet_ok AND edge_confidence='sharp') sharp,
          COUNT(*) FILTER (WHERE sharp_prob IS NOT NULL) had_sharp
        FROM market_observations
        WHERE observed_at >= (SELECT MAX(observed_at) FROM market_observations) - INTERVAL '20 min'
    """)[0]
    print(f"  last snapshot: {last_run['n']} markets | naive would-bet {last_run['naive']} → "
          f"refined {last_run['refined']} (sharp {last_run['sharp']}) | "
          f"sharp line on {last_run['had_sharp']}")


def main():
    print("\n" + "═" * 72)
    print(f"  NOPREDICTIONS — AGENT STATUS    {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
          f"    LIVE_MODE={'ON' if LIVE_MODE else 'OFF'}")
    print("═" * 72)
    pnl_block()
    scans_block()
    last_entries_block()
    next_entries_block()
    shadow_block()
    print("\n" + "═" * 72 + "\n")


if __name__ == "__main__":
    main()
