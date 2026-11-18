#!/usr/bin/env python3
"""
Low Pressure HT Under 0.5 v2 — a goal game that has stayed quiet (paper).

THE RULE (2026-11-10, by the operator's decision)
-------------------------------------------------
Version 1 (ht_under_agent, db/076) buys the first-half Under on the first poll
where the match reads quiet. 126 of its first 330 entries were made on the very
first pressure reading of the fixture, and it stands at -10.5% net. Version 2
is the operator's idea, as far as the data held here can express it: a match
the market priced for goals, in which neither side has created anything for a
while. It buys PM's "1st Half O/U 0.5" UNDER at the ask, 1u, paper, once per
fixture, on the first poll where ALL hold:

  1. everything version 1 asks on that poll: 0-0, minute 15-40, pressure below
     the frozen ceiling for the minute, both books clean (its own gate, reused,
     so the two cannot drift apart on what "quiet now" means);
  2. SUSTAINED: this poll and the five before it (QUIET_STREAK) all read below
     the ceiling for their minute;
  3. NEVER PRESSING: no reading of this fixture since 15' reached PEAK_CEILING
     (19, the bar the Over arm calls pressing);
  4. A GOAL GAME: the first Over 2.5 price seen for the fixture (pre_over25) is
     at least MIN_OVER25. ⚠️ That price is read IN PLAY, at the first poll, not
     before kick-off: pre_is_prematch was true on 3 of 1,658 fixtures. It is a
     0-0 Over 2.5 price around 15', so 0.52 is a higher pre-match price.

Hypothesis H-HT-UNDER-QUIET-V2 (db/091).

WHAT IS KNOWN, AND WHAT IS NOT
------------------------------
Measured 2026-11-09 on obs_version 6-7 rows, first qualifying row per fixture,
price taken as 1 - (Over bid), outcome from the settled row:
  sustained + never pressing: 256 fixtures, +4.4% net (se 6.0)
      before 2026-10-12  126  +8.2%      after  130  +0.8%  (v1, same days: -11.2%)
  ... and Over 2.5 >= 0.52:    96 fixtures, about +12%
      before              50  +16.5% (se 14.4)   after  46  +6.6% (se 16.6)
  ... and Over 2.5 <  0.52:   160 fixtures, about 0%
The streak of 6 and the 0.52 cut were chosen looking at these rows, one variant
tested. None of it is established: under 200 settled bets this is a record, not
a result, and at about 1.6 entries a day that is four months away.

The Over 2.5 bands on their own do NOT hold: >= 0.60 made +11.9% before
2026-10-12 and -37.3% after. That is why this is not a band filter on v1.

It rides on the same rows as v1 (ht_pressure_observations, the under2_*
columns) and makes no network call of its own: the Under ask is the one v1's
gate just read.

⚠️ The streak lives in memory. A restart of the daemon forgets it, which delays
an entry by up to six polls and can never bring one forward.

    python ht_under_v2_agent.py --settle
    python ht_under_v2_agent.py --report
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import db_txn                                                      # noqa: E402
import fav_pressure_agent as fav                                   # noqa: E402
import ht_under_agent as v1                                        # noqa: E402

log = logging.getLogger("ht_under_v2")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Low Pressure HT Under 0.5 v2"
RULE_VERSION = 2

# FROZEN on 2026-11-10 with the hypothesis. Refitting any of them to later rows
# turns the forward test back into a backtest: a change is a version 3.
QUIET_STREAK = 6          # consecutive polls below the ceiling, this one included
PEAK_CEILING = 19.0       # ht_pressure_agent.MIN_PRESSURE, the Over arm's "pressing"
MIN_OVER25 = 0.52         # the first Over 2.5 price seen for the fixture


@dataclass
class UnderV2State:
    """fixture (home, away) -> {streak, peak, at}. Teams, not the fixture id: a
    source switch mid-match re-namespaces the id and would restart the count."""
    seen: dict[tuple, dict] = field(default_factory=dict)

    def prune(self, older_than_s: float = 6 * 3600) -> None:
        cutoff = time.time() - older_than_s
        for key in [k for k, v in self.seen.items() if v["at"] < cutoff]:
            del self.seen[key]


def _note(state: UnderV2State, r: dict) -> dict | None:
    """Fold this poll's reading into the fixture's streak and peak. None outside
    15'-40', where there is no ceiling to read against."""
    bar = v1.pressure_ceiling(r["minute"])
    if bar is None:
        return None
    st = state.seen.setdefault((r["home"], r["away"]), {"streak": 0, "peak": None, "at": 0.0})
    st["at"] = time.time()
    p = r["pressure_now"]
    # No reading is not a quiet reading: it breaks the streak.
    st["streak"] = st["streak"] + 1 if (p is not None and p < bar) else 0
    if p is not None and (st["peak"] is None or p > st["peak"]):
        st["peak"] = p
    return st


def _gate(r: dict, st: dict | None) -> str | None:
    """Why this row is not a v2 entry, or None. Reads v1's decision on the row."""
    if st is None:
        return f"minute {r['minute']} outside {v1.ENTRY_MIN_MINUTE}-{v1.ENTRY_MAX_MINUTE}"
    if not r.get("under_would_enter"):
        return f"v1 gate: {r.get('under_skip_reason') or 'not passed'}"
    if st["streak"] < QUIET_STREAK:
        return f"quiet for {st['streak']} polls, needs {QUIET_STREAK}"
    if st["peak"] is not None and st["peak"] >= PEAK_CEILING:
        return f"pressed earlier (peak {st['peak']:.1f} >= {PEAK_CEILING})"
    if r.get("pre_over25") is None:
        return "no Over 2.5 price for this fixture"
    if r["pre_over25"] < MIN_OVER25 - 1e-9:
        return f"not a goal game (Over 2.5 {r['pre_over25']:.2f} < {MIN_OVER25})"
    if r.get("under_ask") is None:
        return "no Under ask on the row"
    return None


def decide(rows: list[dict], state: UnderV2State) -> int:
    """Put the v2 decision on each first-half row. Must run AFTER v1.decide and
    BEFORE v1.open_trades, which clears under_would_enter on a fixture v1 already
    holds: v2 needs v1's gate, not v1's position."""
    n = 0
    for r in rows:
        st = _note(state, r)
        r["under2_streak"] = st["streak"] if st else None
        reason = _gate(r, st)
        if reason is None:
            r["under2_ask"] = r["under_ask"]
            r["under2_would_enter"] = True
            n += 1
        r["under2_skip_reason"] = reason
    state.prune()
    return n


def strategy_id(conn) -> int | None:
    """None when the strategy is missing or retired: the arm then records its
    decision and buys nothing. It must never take the pressure daemon down."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s AND retired_at IS NULL",
                    (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        log.warning(f"strategy '{STRATEGY_NAME}' missing or retired — observing only (db/091)")
        return None
    return got[0]


def open_trades(conn, sid: int | None, rows: list[dict]) -> int:
    """One 1u paper Under per fixture. Runs before ht.write, so the trade id
    lands on the row that bought it."""
    if sid is None:
        return 0
    opened = 0
    for r in rows:
        if not r.get("under2_would_enter"):
            continue
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ht_pressure_observations "
                "WHERE (fixture_id = %s OR (home = %s AND away = %s)) "
                "  AND under2_entered AND observed_at > now() - interval '6 hours' "
                "LIMIT 1",
                (r["fixture_id"], r["home"], r["away"]),
            )
            if cur.fetchone():
                r["under2_would_enter"] = False
                r["under2_skip_reason"] = "already entered this fixture"
                continue

            q = r["under2_ask"]
            fee = v1.FEE_RATE * q * (1 - q)
            fair = 1.0 - r["fair_base"] if r.get("fair_base") is not None else None
            fair_txt = (f"table fair for the Under {fair:.3f} ({1 / fair:.2f}), "
                        f"{100 * (fair - q - fee):+.1f}pp after the fee — recorded, NOT a gate"
                        if fair else "no table fair for this state")
            reasoning = (
                f"{r['home']} 0-0 {r['away']} {r['minute']}' — 1st Half UNDER 0.5 at "
                f"{q:.3f} ({1 / q:.2f}). Quiet for {r['under2_streak']} polls in a row: "
                f"pressure {r['pressure_now']:.1f} against a ceiling of "
                f"{v1.pressure_ceiling(r['minute'])}, never at {PEAK_CEILING:.0f} since 15'. "
                f"A goal game by its price: Over 2.5 first seen at {r['pre_over25']:.2f} "
                f"(in play, needs {MIN_OVER25}). PREDICTION (H-HT-UNDER-QUIET-V2): the "
                f"Under is still cheap when a goal game has stayed quiet. "
                f"Taker at the ask. {fair_txt}. PAPER."
            )
            cur.execute(
                """INSERT INTO paper_trades
                     (strategy_id, outcome, entry_price, entry_odds, stake_units,
                      model_probability, expected_edge, reasoning, confidence,
                      pm_token_id, pm_live)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)
                   RETURNING id""",
                (sid,
                 f"1st Half Under 0.5 — {r['event_title'] or r['home'] + ' vs ' + r['away']}",
                 q, 1.0 / q, v1.STAKE_UNITS, fair,
                 (fair - q - fee) if fair is not None else None,
                 reasoning, "paper", r["under_token_id"]),
            )
            r["under2_paper_trade_id"] = cur.fetchone()[0]
            r["under2_entered"] = True
            opened += 1
        conn.commit()
    return opened


def settle(conn) -> int:
    """Settle on Polymarket's resolution of the Under token bought, and nothing
    else (see fav._pm_verdicts). A market not yet resolved, or not read, waits."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT pt.id, o.condition_id, o.under_token_id
                 FROM ht_pressure_observations o
                 JOIN paper_trades pt ON pt.id = o.under2_paper_trade_id
                WHERE o.under2_entered AND pt.result IS NULL
                  AND o.observed_at < now() - interval '35 minutes'"""
        )
        pending = cur.fetchall()
    if not pending:
        return 0

    # Every network call FIRST, with no transaction open — see db_txn.py.
    verdicts = fav._pm_verdicts(r["condition_id"] for r in pending)

    settled = 0
    with conn.cursor() as cur:
        for r in pending:
            paid, _ = fav._verdict_of(verdicts, (r["condition_id"], str(r["under_token_id"])))
            if paid is None:
                continue
            fav._pay_trade(cur, r["id"], paid)
            settled += 1
    return settled


def report(conn) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT left(under2_skip_reason, 40) AS reason, count(*) AS n
                 FROM ht_pressure_observations
                WHERE under2_skip_reason IS NOT NULL AND under_would_enter
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""
        )
        rows = cur.fetchall()
        if rows:
            print("\nUnder v2: why no entry, on rows v1's gate passed:")
            for r in rows:
                print(f"  {r['n']:6d}  {r['reason']}")
        cur.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE pt.result = 'won') AS won,
                      count(*) FILTER (WHERE pt.result IS NULL) AS open,
                      sum(pt.payout_units - pt.stake_units)
                          FILTER (WHERE pt.result IS NOT NULL)::float8 AS pnl,
                      sum(%s * (1 - pt.entry_price) * pt.stake_units)
                          FILTER (WHERE pt.result IS NOT NULL)::float8 AS fees,
                      count(*) FILTER (WHERE pt.result IS NOT NULL) AS settled,
                      avg(pt.entry_odds)::float8 AS odds
                 FROM paper_trades pt
                 JOIN strategies s ON s.id = pt.strategy_id
                WHERE s.name = %s""",
            (v1.FEE_RATE, STRATEGY_NAME),
        )
        t = cur.fetchone()
    if t and t["n"]:
        net = (t["pnl"] or 0) - (t["fees"] or 0)
        yld = 100.0 * net / t["settled"] if t["settled"] else 0.0
        print(f"\npaper, {STRATEGY_NAME}: n={t['n']} settled={t['settled']} open={t['open']} "
              f"won={t['won']} mean odds {t['odds']:.2f}  net P&L {net:+.2f}u  "
              f"net yield {yld:+.1f}%")
    print("  (verdict, H-HT-UNDER-QUIET-V2: forward rows only, from 200 settled bets. "
          "Under that it is a record, not a result.)")


def _conn():
    return db_txn.connect(DATABASE_URL)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Low Pressure HT Under 0.5 v2 (paper only)")
    ap.add_argument("--settle", action="store_true", help="settle on PM's resolution")
    ap.add_argument("--report", action="store_true", help="what has been collected")
    args = ap.parse_args()
    conn = _conn()
    try:
        if args.settle:
            log.info(f"settled {settle(conn)} Under v2 trades")
        if args.report:
            report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
