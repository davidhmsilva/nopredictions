"""lab_variants.py — shadow variants of a Create agent: make one, stop one, compare.

Step 2 of agents that review their own record (db/088). A variant is the
agent's spec with a change, run by lab_strategy_runner.py beside the agent on
the same fixtures at the same CLOB prices, in shadow: it never writes
paper_trades, so it can never reach the agent's record, page or the
Marketplace. What it would have bought is a `lab_decisions` row with its
variant_id, which the settle job prices like any other (Polymarket's close, the
CLOB winner).

    cd agent && source ../ingest/.venv/bin/activate
    python lab_variants.py add 23 --change '{"odds_max": 2.6}' --why "the band's edge may run past 2.30"
    python lab_variants.py add 31 --change '{"enter_minutes_before": 180}' --why "draws drift in the last 3h"
    python lab_variants.py list [23]
    python lab_variants.py compare 23
    python lab_variants.py stop 7

The comparison is the agent against each variant on fixtures from the moment the
variant started, and nothing before: the only fair test. A variant that wins is
never swapped in. Promoting it makes a new agent, because the agent's record
was earned on its own rule.

Honesty: under 200 settled entries an arm's P&L is a record, not a result.
CLV (Polymarket's close against the price paid) speaks much sooner, in tens of
entries, and is what to read first.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lab_strategy_runner as lr                                    # noqa: E402

log = logging.getLogger("lab_variants")

MAX_RUNNING = 5                # per agent: a test budget, so twenty tries cannot pick a lucky one
MIN_RESULT = 200               # settled entries before a P&L is a result (CLAUDE.md rule 5)

# What a change may touch: the keys the runner reads. A key it does not read
# would be ignored and the variant would silently run the agent's own rule.
CHANGEABLE = frozenset({
    "market", "side", "leagues", "odds_min", "odds_max", "fav_status",
    "home_team", "away_team", "team", "season_start", "season_end",
    *lr.TEAM_LISTS, "enter_minutes_before",
    *(f"{f}_{e}" for f in lr.FORM_FILTERS for e in ("min", "max")),
    *(f"{p}_{n}_{e}" for p in ("team", "opp") for n in lr.TEAM_FIGURES for e in ("min", "max")),
})


def _conn():
    return lr.db_txn.connect(lr.DATABASE_URL)


def variant_spec(parent: dict, change: dict) -> dict:
    """The parent's spec with `change` applied; a null value removes the key. Pure."""
    spec = dict(parent)
    for k, v in change.items():
        if v is None:
            spec.pop(k, None)
        else:
            spec[k] = v
    return spec


def _same(a: dict, b: dict) -> bool:
    """Two specs that run the same rule: absent and null are the same thing."""
    return {k: v for k, v in a.items() if v is not None} == {k: v for k, v in b.items() if v is not None}


def change_problem(parent: dict, change: dict, running: list[dict]) -> str | None:
    """Why this change cannot become a variant, or None. Pure."""
    if not change:
        return "the change is empty"
    unknown = sorted(set(change) - CHANGEABLE)
    if unknown:
        return f"the runner does not read {', '.join(unknown)}"
    spec = variant_spec(parent, change)
    if _same(spec, parent):
        return "the change leaves the rule as it is"
    why = lr.variant_blocker(spec)
    if why:
        return why
    if len(running) >= MAX_RUNNING:
        return f"this agent already has {MAX_RUNNING} variants running; stop one first"
    if any(_same(spec, r) for r in running):
        return "a running variant already tests this rule"
    return None


def add(conn, strategy_id: int, change: dict, why: str, created_by: str = "operator") -> int:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, spec FROM strategies
                        WHERE id = %s AND source = 'lab' AND retired_at IS NULL""", (strategy_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"#{strategy_id} is not a live agent built on Create")
        parent = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
        if parent.get("kind") == "inplay":
            raise ValueError("live rules have no shadow runner yet; pre-match agents only")
        cur.execute("SELECT spec FROM lab_variants WHERE strategy_id = %s AND status = 'running'",
                    (strategy_id,))
        running = [r["spec"] for r in cur.fetchall()]
    problem = change_problem(parent, change, running)
    if problem:
        raise ValueError(problem)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO lab_variants (strategy_id, spec, change, why, created_by)
                       VALUES (%s, %s::jsonb, %s::jsonb, %s, %s) RETURNING id""",
                    (strategy_id, json.dumps(variant_spec(parent, change)), json.dumps(change),
                     why.strip(), created_by))
        return cur.fetchone()[0]


def stop(conn, variant_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("""UPDATE lab_variants SET status = 'stopped', stopped_at = now()
                        WHERE id = %s AND status = 'running'""", (variant_id,))
        return cur.rowcount == 1


# ── the comparison ───────────────────────────────────────────────────────────

def arm_stats(rows: list[dict]) -> dict:
    """An arm's record from its 'entered' rows (ask, won, close_price,
    close_source). 1u flat at the ask. Pure.

    clv = Polymarket's close / the price paid − 1, only where a real close was
    read. The close is a mid and the entry an ask, so CLV carries the half-spread
    paid: an arm that only matches the market shows slightly below zero."""
    settled = [r for r in rows if r.get("won") is not None and r.get("ask")]
    pnl = sum((1 / float(r["ask"]) - 1) if r["won"] else -1.0 for r in settled)
    clvs = [float(r["close_price"]) / float(r["ask"]) - 1 for r in rows
            if r.get("close_source") == "prices_history_1m" and r.get("close_price") and r.get("ask")]
    out = {"entries": len(rows), "settled": len(settled),
           "wins": sum(1 for r in settled if r["won"]),
           "pnl": round(pnl, 3), "roi": round(pnl / len(settled), 4) if settled else None,
           "clv_n": len(clvs), "clv": None, "clv_ci": None}
    if clvs:
        mean = sum(clvs) / len(clvs)
        out["clv"] = round(mean, 4)
        if len(clvs) >= 2:
            sd = math.sqrt(sum((c - mean) ** 2 for c in clvs) / (len(clvs) - 1))
            out["clv_ci"] = round(1.96 * sd / math.sqrt(len(clvs)), 4)
    return out


def _entries(conn, strategy_id: int, variant_id: int | None, since) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT event_title, kickoff, ask, won, close_price, close_source
                         FROM lab_decisions
                        WHERE strategy_id = %s AND variant_id IS NOT DISTINCT FROM %s
                          AND stage = 'entered' AND kickoff >= %s
                        ORDER BY kickoff""", (strategy_id, variant_id, since))
        return [dict(r) for r in cur.fetchall()]


def compare(conn, strategy_id: int) -> list[dict]:
    """The agent against each of its variants, each pair from the variant's
    start. Returned for the weekly review; printed by the CLI."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, change, why, status, created_at, stopped_at FROM lab_variants
                        WHERE strategy_id = %s ORDER BY id""", (strategy_id,))
        variants = [dict(r) for r in cur.fetchall()]
    out = []
    for v in variants:
        mine = _entries(conn, strategy_id, v["id"], v["created_at"])
        if v["stopped_at"]:
            mine = [r for r in mine if r["kickoff"] <= v["stopped_at"]]
        theirs = [r for r in _entries(conn, strategy_id, None, v["created_at"])
                  if not v["stopped_at"] or r["kickoff"] <= v["stopped_at"]]
        both = ({(r["event_title"], r["kickoff"]) for r in mine}
                & {(r["event_title"], r["kickoff"]) for r in theirs})
        out.append({"variant_id": v["id"], "change": v["change"], "why": v["why"],
                    "status": v["status"], "since": v["created_at"],
                    "agent": arm_stats(theirs), "variant": arm_stats(mine), "shared": len(both)})
    return out


def _line(name: str, s: dict) -> str:
    roi = f"{s['roi']:+.1%}" if s["roi"] is not None else "—"
    clv = (f"{s['clv']:+.2%}" + (f" ±{s['clv_ci']:.2%}" if s["clv_ci"] is not None else "")
           if s["clv"] is not None else "—")
    tag = "" if s["settled"] >= MIN_RESULT else "  (a record, not a result)" if s["settled"] else ""
    return (f"    {name:<8} {s['entries']:>4} entries · {s['settled']:>4} settled · "
            f"{s['wins']:>3} won · {s['pnl']:+7.2f}u · ROI {roi:>7} · CLV {clv} (n={s['clv_n']}){tag}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="start a shadow variant of an agent")
    a.add_argument("strategy_id", type=int)
    a.add_argument("--change", required=True, help="JSON {key: new value}; null removes a key")
    a.add_argument("--why", required=True, help="the hypothesis, in words")
    s = sub.add_parser("stop", help="stop a variant")
    s.add_argument("variant_id", type=int)
    li = sub.add_parser("list", help="variants, of one agent or all")
    li.add_argument("strategy_id", type=int, nargs="?")
    c = sub.add_parser("compare", help="an agent against its variants, from each variant's start")
    c.add_argument("strategy_id", type=int)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    conn = _conn()

    if args.cmd == "add":
        try:
            vid = add(conn, args.strategy_id, json.loads(args.change), args.why)
        except ValueError as exc:
            sys.exit(f"not added: {exc}")
        print(f"v{vid} running in shadow beside #{args.strategy_id} from the next cycle")
    elif args.cmd == "stop":
        print("stopped" if stop(conn, args.variant_id) else "no running variant with that id")
    elif args.cmd == "list":
        with conn.cursor() as cur:
            cur.execute("""SELECT v.id, v.strategy_id, s.name, v.status, v.change, v.why, v.created_at::date,
                                  v.blocker
                             FROM lab_variants v JOIN strategies s ON s.id = v.strategy_id
                            WHERE %s::int IS NULL OR v.strategy_id = %s ORDER BY v.id""",
                        (args.strategy_id, args.strategy_id))
            for vid, sid, name, status, change, why, day, blocker in cur.fetchall():
                print(f"v{vid}  #{sid} {name[:40]:<40} {status:<8} since {day}  {json.dumps(change)}")
                print(f"      {why}" + (f"  [blocked: {blocker}]" if blocker else ""))
    elif args.cmd == "compare":
        for r in compare(conn, args.strategy_id):
            print(f"v{r['variant_id']} {json.dumps(r['change'])} [{r['status']}] since "
                  f"{r['since']:%Y-%m-%d %H:%M}Z — {r['why']}")
            print(_line("agent", r["agent"]))
            print(_line("variant", r["variant"]))
            print(f"    {r['shared']} fixtures entered by both")


if __name__ == "__main__":
    main()
