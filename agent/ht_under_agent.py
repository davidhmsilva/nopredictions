#!/usr/bin/env python3
"""
Low Pressure HT Under 0.5 — the other side of the first-half arm (paper).

THE RULE (2026-10-12, by the operator's decision)
-------------------------------------------------
A fixture is still 0-0 between 15' and 40', and the match is QUIET for the
minute it is in: its pressure (ht_pressure_agent.current_pressure, both ends,
the very reading s17 gates on) is in the bottom third for that minute
(LOW_PRESS_BAR, frozen). Buy PM's "1st Half O/U 0.5" UNDER at the ask, 1u,
paper, once per fixture. Hypothesis H-HT-UNDER-QUIET (db/076).

WHY THE UNDER
-------------
On 1,379 clean-book fixtures, outcome from PM's own resolution, the Over's ask
sits 3.3pp above what happens and the Under's is fair (+0.03pp): the market's
mistake is on the Over side. Bought at the ask, the Under loses about the fee
(-2.5%). In quiet games (bottom third of pressure for the minute) it made +0.9%
(n=479, CI[-8,+10]). On the current axis (obs_version 6-7) with this exact bar
it made +4.1% on 279 fixtures, but the bar was fitted on those rows, so that
number is in-sample. None of it is established; the forward record is the test.

It rides on s17's rows: same poll, same table (ht_pressure_observations, the
under_* columns), so it costs no api-football call. The only extra network call
is one Under book read per candidate, because 1 - (Over bid) is the Under's ask
only on a book that is not moving.

⚠️ Taker only. A resting Under bid is filled the instant a goal makes the Under
worthless, and a one-minute poll cannot cancel in time.

    python ht_under_agent.py --settle     # settle on PM's resolution
    python ht_under_agent.py --report     # what has been collected
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import db_txn                                                      # noqa: E402
import fav_pressure_agent as fav                                   # noqa: E402
from late_goals_observer import _fetch_book                        # noqa: E402

log = logging.getLogger("ht_under")

DATABASE_URL = os.getenv("DATABASE_URL")
STRATEGY_NAME = "Low Pressure HT Under 0.5"
RULE_VERSION = 1

ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE = 15, 40

# The 1/3 quantile of s17's pressure_now per minute bucket, on clean-book rows of
# obs_version 6-7 (the current axis: xG estimated from shots since 2026-09-11),
# FROZEN on 2026-10-12. Refitting it to later rows would turn the forward test
# back into a backtest. On the ESPN-era rows it fires on ~5.8 fixtures a day.
LOW_PRESS_BAR = (
    (15, 19, 11.4),
    (20, 24, 13.6),
    (25, 29, 12.8),
    (30, 34, 12.5),
    (35, 40, 13.2),
)

# The book gates of the tested population (s17's clean book), plus the same
# limits on the Under's own book, which is the one actually bought.
MAX_SPREAD = 0.06
MIN_DEPTH_USD = 25.0
MAX_ASK = 0.85
STAKE_UNITS = 1.0
FEE_RATE = 0.05                 # Polymarket soccer taker, rate x p(1-p) per share


def pressure_ceiling(minute: int) -> float | None:
    """The pressure a match must stay BELOW at this minute; None outside 15'-40'."""
    for lo, hi, bar in LOW_PRESS_BAR:
        if lo <= minute <= hi:
            return bar
    return None


def _gate(r: dict) -> str | None:
    """Why this row is not an Under candidate, or None. No network."""
    if r["goals_total"]:
        return f"not 0-0 ({r['home_goals']}-{r['away_goals']})"
    bar = pressure_ceiling(r["minute"])
    if bar is None:
        return f"minute {r['minute']} outside {ENTRY_MIN_MINUTE}-{ENTRY_MAX_MINUTE}"
    if not r.get("under_token_id"):
        return "no Under token"
    if r["pressure_now"] is None:
        return "no pressure reading"
    if r["pressure_now"] >= bar:
        return f"not quiet enough for {r['minute']}' ({r['pressure_now']:.1f} >= {bar})"
    if r["best_bid"] is None or r["best_ask"] is None:
        return "no two-sided Over book"
    if r["best_ask"] - r["best_bid"] > MAX_SPREAD + 1e-9:
        return f"Over spread {100 * (r['best_ask'] - r['best_bid']):.0f}pp > {100 * MAX_SPREAD:.0f}pp"
    if (r["ask_depth_usd"] or 0) < MIN_DEPTH_USD:
        return f"Over ask depth ${r['ask_depth_usd'] or 0:.0f} < ${MIN_DEPTH_USD:.0f}"
    if r["best_ask"] > MAX_ASK:
        return f"Over ask {r['best_ask']:.2f} > {MAX_ASK}"
    if r["score_agrees"] is False:
        return "score disagreement"
    return None


def _book_gate(book: dict | None) -> str | None:
    if not book:
        return "no Under book"
    if book["best_ask"] > MAX_ASK:
        return f"Under ask {book['best_ask']:.2f} > {MAX_ASK}"
    if book["best_ask"] - book["best_bid"] > MAX_SPREAD + 1e-9:
        return f"Under spread {100 * (book['best_ask'] - book['best_bid']):.0f}pp > {100 * MAX_SPREAD:.0f}pp"
    if (book["ask_depth_usd"] or 0) < MIN_DEPTH_USD:
        return f"Under ask depth ${book['ask_depth_usd'] or 0:.0f} < ${MIN_DEPTH_USD:.0f}"
    return None


def decide(rows: list[dict], fetch_book=_fetch_book) -> int:
    """Put the Under decision on each of s17's rows. The Under book is read
    only for a row that passes every other gate."""
    n = 0
    for r in rows:
        reason = _gate(r)
        if reason is None:
            book = fetch_book({"token_id": r["under_token_id"]})
            reason = _book_gate(book)
            if reason is None:
                r["under_ask"] = book["best_ask"]
                r["under_would_enter"] = True
                n += 1
        r["under_skip_reason"] = reason
    return n


def strategy_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM strategies WHERE name = %s AND retired_at IS NULL",
                    (STRATEGY_NAME,))
        got = cur.fetchone()
    if not got:
        raise SystemExit(f"strategy '{STRATEGY_NAME}' missing or retired — apply db/076")
    return got[0]


def open_trades(conn, sid: int, rows: list[dict]) -> int:
    """One 1u paper Under per fixture. Runs before ht.write, so the trade id
    lands on the row that bought it."""
    opened = 0
    for r in rows:
        if not r["under_would_enter"]:
            continue
        with conn.cursor() as cur:
            # Teams as well as the id: a source switch mid-match re-namespaces
            # the fixture id (ESPN ids are negative), and an id-only guard would
            # buy the same match twice. The unique index is the backstop.
            cur.execute(
                "SELECT 1 FROM ht_pressure_observations "
                "WHERE (fixture_id = %s OR (home = %s AND away = %s)) "
                "  AND under_entered AND observed_at > now() - interval '6 hours' "
                "LIMIT 1",
                (r["fixture_id"], r["home"], r["away"]),
            )
            if cur.fetchone():
                r["under_would_enter"] = False
                r["under_skip_reason"] = "already entered this fixture"
                continue

            q = r["under_ask"]
            fee = FEE_RATE * q * (1 - q)
            fair = 1.0 - r["fair_base"] if r["fair_base"] is not None else None
            fair_txt = (f"table fair for the Under {fair:.3f} ({1 / fair:.2f}), "
                        f"{100 * (fair - q - fee):+.1f}pp after the fee — recorded, NOT a gate"
                        if fair else "no table fair for this state")
            reasoning = (
                f"{r['home']} 0-0 {r['away']} {r['minute']}' — 1st Half UNDER 0.5 at "
                f"{q:.3f} ({1 / q:.2f}). The match is quiet for the minute: pressure "
                f"{r['pressure_now']:.1f} against a ceiling of {pressure_ceiling(r['minute'])} "
                f"(the bottom third, frozen 2026-10-12). PREDICTION (H-HT-UNDER-QUIET): "
                f"in a quiet first half the Under is not overpriced the way the Over is. "
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
                 q, 1.0 / q, STAKE_UNITS, fair,
                 (fair - q - fee) if fair is not None else None,
                 reasoning, "paper", r["under_token_id"]),
            )
            r["under_paper_trade_id"] = cur.fetchone()[0]
            r["under_entered"] = True
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
                 JOIN paper_trades pt ON pt.id = o.under_paper_trade_id
                WHERE o.under_entered AND pt.result IS NULL
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
            """SELECT under_skip_reason AS reason, count(*) AS n
                 FROM ht_pressure_observations
                WHERE under_skip_reason IS NOT NULL AND goals_total = 0
                  AND minute BETWEEN %s AND %s
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
            (ENTRY_MIN_MINUTE, ENTRY_MAX_MINUTE),
        )
        rows = cur.fetchall()
        if rows:
            print("\nUnder: why no entry, 0-0 inside 15-40':")
            for r in rows:
                print(f"  {r['n']:6d}  {r['reason'][:70]}")
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
            (FEE_RATE, STRATEGY_NAME),
        )
        t = cur.fetchone()
    if t and t["n"]:
        net = (t["pnl"] or 0) - (t["fees"] or 0)
        yld = 100.0 * net / t["settled"] if t["settled"] else 0.0
        print(f"\npaper, {STRATEGY_NAME}: n={t['n']} settled={t['settled']} open={t['open']} "
              f"won={t['won']} mean odds {t['odds']:.2f}  net P&L {net:+.2f}u  "
              f"net yield {yld:+.1f}%")
    print("  (verdict, H-HT-UNDER-QUIET: forward rows only. Separation, quiet vs pressing "
          "Under yield on observation rows, needs ~1,000 fixtures a side; money, +2% at "
          "odds ~2.0, needs ~10,000 bets.)")


def _conn():
    return db_txn.connect(DATABASE_URL)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Low Pressure HT Under 0.5 (paper only)")
    ap.add_argument("--settle", action="store_true", help="settle on PM's resolution")
    ap.add_argument("--report", action="store_true", help="what has been collected")
    args = ap.parse_args()
    conn = _conn()
    try:
        if args.settle:
            log.info(f"settled {settle(conn)} Under trades")
        if args.report:
            report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
