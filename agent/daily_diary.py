"""
Daily Diary — end-of-day summary of everything the NOPREDICTIONS agent did.

Queries the DB for trades placed, trades resolved, agent runs, and P&L,
then writes a formatted Markdown diary entry to agent/diary/.

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python daily_diary.py                # today's diary
  python daily_diary.py --date 2026-05-14  # specific date
  python daily_diary.py --stdout       # print to terminal instead of file
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

DATABASE_URL = os.getenv("DATABASE_URL")
DIARY_DIR = os.path.join(os.path.dirname(__file__), "diary")


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _d(v, places=2):
    """Format a Decimal/float for display."""
    if v is None:
        return "—"
    return f"{float(v):.{places}f}"


def _pct(v):
    if v is None:
        return "—"
    return f"{float(v) * 100:+.1f}pp"


def _odds(v):
    if v is None:
        return "—"
    return f"@{float(v):.2f}"


# ─── Queries ──────────────────────────────────────────────────────────────────

def fetch_trades_placed(conn, target_date: date) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            pt.id, s.name AS strategy, pt.outcome,
            pm.title AS market,
            pt.entry_price, pt.entry_odds,
            pt.model_probability, pt.expected_edge,
            pt.confidence, pt.stake_units,
            pt.reasoning, pt.placed_at
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        LEFT JOIN pm_markets pm ON pm.id = pt.market_id
        WHERE pt.placed_at::date = %s
        ORDER BY pt.placed_at
    """, (target_date,))
    return [dict(r) for r in cur.fetchall()]


def fetch_trades_resolved(conn, target_date: date) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            pt.id, s.name AS strategy, pt.outcome,
            pm.title AS market,
            pt.entry_price, pt.entry_odds,
            pt.result, pt.payout_units, pt.stake_units,
            pt.clv, pt.closing_price,
            pt.resolved_at
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        LEFT JOIN pm_markets pm ON pm.id = pt.market_id
        WHERE pt.resolved_at::date = %s
        ORDER BY pt.resolved_at
    """, (target_date,))
    return [dict(r) for r in cur.fetchall()]


def fetch_agent_runs(conn, target_date: date) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            id, run_type, status,
            started_at, finished_at,
            output_summary
        FROM agent_runs
        WHERE started_at::date = %s
        ORDER BY started_at
    """, (target_date,))
    return [dict(r) for r in cur.fetchall()]


def fetch_open_trades(conn) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            pt.id, s.name AS strategy, pt.outcome,
            pm.title AS market,
            pt.entry_price, pt.entry_odds,
            pt.expected_edge, pt.placed_at
        FROM paper_trades pt
        JOIN strategies s ON s.id = pt.strategy_id
        LEFT JOIN pm_markets pm ON pm.id = pt.market_id
        WHERE pt.result IS NULL
        ORDER BY pt.placed_at DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def fetch_cumulative_stats(conn, up_to_date: date) -> dict:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            COUNT(*) AS total_trades,
            COUNT(*) FILTER (WHERE result IS NOT NULL) AS settled,
            COUNT(*) FILTER (WHERE result IS NULL) AS pending,
            COUNT(*) FILTER (WHERE result = 'won') AS wins,
            COUNT(*) FILTER (WHERE result = 'lost') AS losses,
            COUNT(*) FILTER (WHERE result = 'void') AS voids,
            ROUND(SUM(COALESCE(payout_units, 0) - COALESCE(stake_units, 0))
                  FILTER (WHERE result IN ('won', 'lost')), 2) AS net_pnl,
            ROUND(SUM(stake_units)
                  FILTER (WHERE result IN ('won', 'lost')), 2) AS total_staked,
            ROUND(AVG(clv) FILTER (WHERE clv IS NOT NULL), 4) AS avg_clv,
            ROUND(AVG(expected_edge) FILTER (WHERE expected_edge IS NOT NULL), 4) AS avg_edge
        FROM paper_trades
        WHERE placed_at::date <= %s
    """, (up_to_date,))
    row = cur.fetchone()
    return dict(row) if row else {}


def fetch_strategy_breakdown(conn, target_date: date) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            s.name AS strategy,
            COUNT(*) FILTER (WHERE pt.placed_at::date = %s) AS placed_today,
            COUNT(*) FILTER (WHERE pt.resolved_at::date = %s) AS resolved_today,
            COUNT(*) FILTER (WHERE pt.result = 'won' AND pt.resolved_at::date = %s) AS wins_today,
            COUNT(*) FILTER (WHERE pt.result = 'lost' AND pt.resolved_at::date = %s) AS losses_today,
            ROUND(SUM(COALESCE(pt.payout_units, 0) - COALESCE(pt.stake_units, 0))
                  FILTER (WHERE pt.result IN ('won', 'lost') AND pt.resolved_at::date = %s), 2) AS pnl_today,
            ROUND(AVG(pt.clv)
                  FILTER (WHERE pt.clv IS NOT NULL AND pt.resolved_at::date = %s), 4) AS avg_clv_today
        FROM strategies s
        LEFT JOIN paper_trades pt ON pt.strategy_id = s.id
        WHERE s.retired_at IS NULL
        GROUP BY s.id, s.name
        ORDER BY placed_today DESC
    """, (target_date, target_date, target_date, target_date, target_date, target_date))
    return [dict(r) for r in cur.fetchall()]


# ─── Render ───────────────────────────────────────────────────────────────────

def render_diary(target_date: date, placed: list, resolved: list,
                 runs: list, open_trades: list, cumulative: dict,
                 by_strategy: list) -> str:
    lines = []
    w = lines.append

    w(f"# NOPREDICTIONS — Daily Diary: {target_date.isoformat()}")
    w("")

    # ── Summary box ──
    total_placed = len(placed)
    total_resolved = len(resolved)
    wins = sum(1 for t in resolved if t["result"] == "won")
    losses = sum(1 for t in resolved if t["result"] == "lost")
    day_pnl = sum(
        float(t["payout_units"] or 0) - float(t["stake_units"] or 0)
        for t in resolved if t["result"] in ("won", "lost")
    )

    w("## Day at a Glance")
    w("")
    w(f"| Metric | Value |")
    w(f"|---|---|")
    w(f"| Trades placed | **{total_placed}** |")
    w(f"| Trades resolved | **{total_resolved}** ({wins}W / {losses}L) |")
    w(f"| Day P&L | **{day_pnl:+.2f}u** |")
    if total_resolved > 0:
        yield_pct = day_pnl / sum(float(t["stake_units"] or 1) for t in resolved if t["result"] in ("won", "lost")) * 100 if any(t["result"] in ("won", "lost") for t in resolved) else 0
        w(f"| Day yield | **{yield_pct:+.1f}%** |")
        avg_clv_day = [float(t["clv"]) for t in resolved if t["clv"] is not None]
        if avg_clv_day:
            w(f"| Avg CLV (resolved today) | **{sum(avg_clv_day)/len(avg_clv_day)*100:+.2f}pp** |")
    w(f"| Open positions | **{len(open_trades)}** |")
    w(f"| Agent runs | **{len(runs)}** |")
    w("")

    # ── Strategy breakdown ──
    active = [s for s in by_strategy if (s["placed_today"] or 0) > 0 or (s["resolved_today"] or 0) > 0]
    if active:
        w("## Strategy Breakdown")
        w("")
        w("| Strategy | Placed | Resolved | W/L | P&L | Avg CLV |")
        w("|---|---|---|---|---|---|")
        for s in active:
            placed_n = s["placed_today"] or 0
            resolved_n = s["resolved_today"] or 0
            wins_n = s["wins_today"] or 0
            losses_n = s["losses_today"] or 0
            pnl = float(s["pnl_today"]) if s["pnl_today"] else 0
            clv = f"{float(s['avg_clv_today'])*100:+.2f}pp" if s["avg_clv_today"] else "—"
            w(f"| {s['strategy']} | {placed_n} | {resolved_n} | {wins_n}W/{losses_n}L | {pnl:+.2f}u | {clv} |")
        w("")

    # ── Trades placed ──
    if placed:
        w("## Trades Placed")
        w("")
        for t in placed:
            edge_pp = float(t["expected_edge"] or 0) * 100
            w(f"- **{t['outcome']}** on _{t['market'] or 'unknown'}_")
            w(f"  - Strategy: {t['strategy']} | Entry: {_odds(t['entry_odds'])} ({_d(float(t['entry_price'] or 0)*100, 1)}¢)")
            w(f"  - Model prob: {_d(float(t['model_probability'] or 0)*100, 1)}% | Edge: {edge_pp:+.1f}pp | Stake: {_d(t['stake_units'])}u")
            if t.get("reasoning"):
                reason = t["reasoning"][:200]
                w(f"  - Reasoning: {reason}{'…' if len(t['reasoning'] or '') > 200 else ''}")
            w("")
    else:
        w("## Trades Placed")
        w("")
        w("_No trades placed today._")
        w("")

    # ── Trades resolved ──
    if resolved:
        w("## Trades Resolved")
        w("")
        for t in resolved:
            result_emoji = {"won": "WIN", "lost": "LOSS", "void": "VOID"}.get(t["result"], t["result"])
            payout = float(t["payout_units"] or 0)
            clv_str = f"CLV: {float(t['clv'])*100:+.2f}pp" if t["clv"] is not None else "CLV: —"
            w(f"- **[{result_emoji}]** {t['outcome']} on _{t['market'] or 'unknown'}_ → {payout:+.2f}u | {clv_str}")
        w("")
    else:
        w("## Trades Resolved")
        w("")
        w("_No trades resolved today._")
        w("")

    # ── Agent runs ──
    if runs:
        w("## Agent Runs")
        w("")
        w("| Time (UTC) | Agent | Status | Summary |")
        w("|---|---|---|---|")
        for r in runs:
            time_str = r["started_at"].strftime("%H:%M") if r["started_at"] else "?"
            summary = (r["output_summary"] or "")[:80]
            w(f"| {time_str} | {r['run_type']} | {r['status']} | {summary} |")
        w("")
    else:
        w("## Agent Runs")
        w("")
        w("_No agent runs logged today._")
        w("")

    # ── Cumulative ──
    w("## Cumulative Performance (all time)")
    w("")
    c = cumulative
    total = c.get("total_trades", 0) or 0
    settled = c.get("settled", 0) or 0
    pending = c.get("pending", 0) or 0
    wins_all = c.get("wins", 0) or 0
    losses_all = c.get("losses", 0) or 0
    net_pnl = float(c.get("net_pnl", 0) or 0)
    total_staked = float(c.get("total_staked", 0) or 0)
    avg_clv = c.get("avg_clv")
    avg_edge = c.get("avg_edge")

    w(f"| Metric | Value |")
    w(f"|---|---|")
    w(f"| Total trades | {total} ({settled} settled, {pending} pending) |")
    w(f"| Record | {wins_all}W / {losses_all}L |")
    if settled > 0 and total_staked > 0:
        w(f"| Net P&L | {net_pnl:+.2f}u |")
        w(f"| Total staked | {total_staked:.2f}u |")
        w(f"| Yield | {net_pnl/total_staked*100:+.1f}% |")
    if avg_clv is not None:
        w(f"| Avg CLV | {float(avg_clv)*100:+.2f}pp |")
    if avg_edge is not None:
        w(f"| Avg edge at pick | {float(avg_edge)*100:+.1f}pp |")
    w("")

    # ── Open positions ──
    if open_trades:
        w(f"## Open Positions ({len(open_trades)})")
        w("")
        for t in open_trades[:20]:
            edge_pp = float(t["expected_edge"] or 0) * 100
            placed_str = t["placed_at"].strftime("%Y-%m-%d") if t["placed_at"] else "?"
            w(f"- {t['outcome']} on _{t['market'] or 'unknown'}_ | {t['strategy']} | {_odds(t['entry_odds'])} | Edge: {edge_pp:+.1f}pp | Placed: {placed_str}")
        if len(open_trades) > 20:
            w(f"- _…and {len(open_trades) - 20} more_")
        w("")

    w("---")
    w(f"_Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NOPREDICTIONS daily diary")
    parser.add_argument("--date", type=str, default=None,
                        help="Date to report on (YYYY-MM-DD). Defaults to today UTC.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print to terminal instead of writing a file.")
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = datetime.now(timezone.utc).date()

    conn = _conn()
    try:
        placed = fetch_trades_placed(conn, target_date)
        resolved = fetch_trades_resolved(conn, target_date)
        runs = fetch_agent_runs(conn, target_date)
        open_trades = fetch_open_trades(conn)
        cumulative = fetch_cumulative_stats(conn, target_date)
        by_strategy = fetch_strategy_breakdown(conn, target_date)
    finally:
        conn.close()

    diary = render_diary(target_date, placed, resolved, runs,
                         open_trades, cumulative, by_strategy)

    if args.stdout:
        print(diary)
    else:
        os.makedirs(DIARY_DIR, exist_ok=True)
        path = os.path.join(DIARY_DIR, f"{target_date.isoformat()}.md")
        with open(path, "w") as f:
            f.write(diary)
        print(f"Diary written to {path}")


if __name__ == "__main__":
    main()
