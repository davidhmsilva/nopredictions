"""
flb_scanner.py — Strategy: favourite-longshot bias (H-FLB, research_hypotheses id=25).

THE EDGE
--------
Polymarket football prices show textbook favourite-longshot bias. Measured
2026-07-21 over 5,572 CLOB-resolved markets joined to market_observations, one
snapshot per market 60-360 min before kickoff, buying AT THE ASK:

    ask 0.10-0.20   -20.6%   CI [-38.6,  -2.2]   longshots overpriced
    ask 0.20-0.50    ~ 0%
    ask 0.50-0.80    +8.8%   CI [ +2.2, +15.2]   n=775 / 251 matches, P(>0)=1.00

This is a SETTLEMENT edge, not a price-movement edge — which is exactly why it
survives. Every price-movement strategy we tested died on the spread floor
(hyp id=24): pre-match PM football moves ~0.5-2pp while a round trip costs
~1.2-2.5pp. Here the ask is paid ONCE at entry and the position is held to
resolution, so the +8.8% is already net of the spread.

WHY WE BELIEVE IT ISN'T NOISE
    * monotone across the whole price ladder, as FLB predicts — not an isolated pocket
    * clears the 200-selection rule (251 matches)
    * NOT a World Cup artifact: WC-only +2.8% (ns), non-WC +9.7% CI [+1.8,+17.5]
    * broad-based across totals / 1x2 / btts / handicap / halftime

KNOWN WEAKNESSES — read before scaling
    * the band was chosen post-hoc. It is now FROZEN. Do not re-tune it.
    * second-half time split is only +1.4% (n=59 matches — thin, not damning)
    * dies at a 2pp cost haircut: +5.4% CI [-0.9, +11.6]
    => micro stakes only, and re-score with flb_eval before any scale-up.

This scanner is PAPER-ONLY by design. It writes paper_trades and never places
an order. Going live is a deliberate human decision, not a flag this file flips.

    python flb_scanner.py              # scan and log paper positions
    python flb_scanner.py --dry-run    # print picks, write nothing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dc_scanner import _fetch_pm_events, _upsert_pm_market  # noqa: E402

log = logging.getLogger(__name__)
log.propagate = False
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [flb_scanner] %(message)s", "%H:%M:%S"))
log.addHandler(_h)
log.setLevel(logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")
CLOB_BOOK = "https://clob.polymarket.com/book"

HYPOTHESIS_ID = 25            # research_hypotheses row this strategy implements
STRATEGY_NAME = "FLB Pre-Match"
STAKE_UNITS = 1.0

# ── FROZEN PARAMETERS (hypothesis id=25) — changing these invalidates the test ─
ASK_LO, ASK_HI = 0.50, 0.80
MAX_SPREAD = 0.05
MIN_MINUTES_TO_KO, MAX_MINUTES_TO_KO = 60, 360
MIN_DEPTH_USD = 100.0            # top-5 ask notional; we stake ~$1-2
EXCLUDED_PATTERNS = ("halftime result",)   # ht_home_win was -7.4% in discovery


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_soccer(event: dict) -> bool:
    """Gamma's 'Soccer' tag. The naive '<x> vs <y>' title test lets in Valorant,
    Counter-Strike and Mobile Legends — the edge was measured on football only.
    """
    for tag in event.get("tags") or []:
        if str(tag.get("label") or tag.get("slug") or "").strip().lower() == "soccer":
            return True
    return False


def _candidates() -> list[dict]:
    """Football tokens whose kickoff sits in the frozen entry window."""
    now = datetime.now(timezone.utc)
    lo = now + timedelta(minutes=MIN_MINUTES_TO_KO)
    hi = now + timedelta(minutes=MAX_MINUTES_TO_KO)

    out = []
    for event in _fetch_pm_events(days_ahead=2):
        title = event.get("title", "")
        if " vs" not in title or not _is_soccer(event):
            continue
        for mkt in event.get("markets", []):
            if mkt.get("closed") or not mkt.get("clobTokenIds"):
                continue
            ko = _parse_ts(mkt.get("endDate") or event.get("endDate"))
            if not ko or not (lo <= ko <= hi):
                continue
            q = (mkt.get("question") or "").lower()
            if any(p in q for p in EXCLUDED_PATTERNS):
                continue
            try:
                ids = json.loads(mkt["clobTokenIds"])
                outcomes = json.loads(mkt.get("outcomes") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not ids:
                continue
            # YES token ONLY. Discovery tracked one token per PM market (the
            # observer records outcomePrices[0]), so taking both sides would not
            # reproduce the measured edge — and when both sides sit inside
            # 0.50-0.80 it buys a certain 1.00 payout for ~1.04. Guaranteed loss.
            out.append({
                "token_id": ids[0], "condition_id": mkt.get("conditionId"),
                "event_title": title, "question": mkt.get("question"),
                "outcome": outcomes[0] if outcomes else None,
                "kickoff": ko,
            })
    return out


def _quote(tok: dict) -> dict | None:
    """Top-of-book for a token, with the frozen entry filters applied."""
    try:
        book = requests.get(CLOB_BOOK, params={"token_id": tok["token_id"]}, timeout=10).json()
    except Exception:
        return None
    bids = sorted(book.get("bids") or [], key=lambda x: -_f(x["price"], 0))[:5]
    asks = sorted(book.get("asks") or [], key=lambda x: _f(x["price"], 1))[:5]
    if not bids or not asks:
        return None

    bid, ask = _f(bids[0]["price"]), _f(asks[0]["price"])
    depth = sum(_f(x["price"], 0) * _f(x["size"], 0) for x in asks)
    if not (ASK_LO <= ask <= ASK_HI):
        return None
    if ask <= bid or (ask - bid) > MAX_SPREAD or depth < MIN_DEPTH_USD:
        return None
    return {**tok, "bid": bid, "ask": ask, "depth": depth}


def _strategy_id(cur) -> int:
    cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO strategies (hypothesis_id, name, rules) VALUES (%s,%s,%s) RETURNING id",
        (HYPOTHESIS_ID, STRATEGY_NAME, json.dumps({
            "edge": "favourite-longshot bias — buy PM football at the ask, hold to settlement",
            "ask_band": [ASK_LO, ASK_HI],
            "minutes_to_kickoff": [MIN_MINUTES_TO_KO, MAX_MINUTES_TO_KO],
            "max_spread": MAX_SPREAD,
            "min_ask_depth_usd": MIN_DEPTH_USD,
            "excluded": list(EXCLUDED_PATTERNS),
            "token": "YES only (outcome index 0)",
            "sport_filter": "Gamma 'Soccer' tag",
            "mode": "PAPER ONLY",
        })))
    return cur.fetchone()[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="FLB pre-match scanner (paper only)")
    ap.add_argument("--dry-run", action="store_true", help="print picks, write nothing")
    ap.add_argument("--max-per-match", type=int, default=3,
                    help="cap correlated positions per match (0 = uncapped, default 3)")
    args = ap.parse_args()

    if not DATABASE_URL and not args.dry_run:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    cands = _candidates()
    log.info(f"{len(cands)} tokens with kickoff in "
             f"{MIN_MINUTES_TO_KO}-{MAX_MINUTES_TO_KO} min window")

    with ThreadPoolExecutor(max_workers=16) as pool:
        picks = [p for p in pool.map(_quote, cands) if p]
    log.info(f"{len(picks)} pass the frozen filters (ask {ASK_LO}-{ASK_HI}, "
             f"spread<={MAX_SPREAD}, depth>=${MIN_DEPTH_USD:.0f})")

    # RISK CONTROL, not part of the edge claim. A single match can throw 7
    # qualifying markets (O/U 0.5, 1.5, 2.5, BTTS, team totals...) which all
    # resolve off the same goals — 47 positions over 8 matches is an effective
    # sample of 8. Rank by ask-side depth so the tie-break is execution quality,
    # never anything correlated with the outcome.
    if args.max_per_match:
        kept: dict[str, int] = {}
        capped = []
        for p in sorted(picks, key=lambda x: -x["depth"]):
            n = kept.get(p["event_title"], 0)
            if n >= args.max_per_match:
                continue
            kept[p["event_title"]] = n + 1
            capped.append(p)
        if len(capped) < len(picks):
            log.info(f"capped {len(picks)} -> {len(capped)} positions "
                     f"(max {args.max_per_match}/match across {len(kept)} matches)")
        picks = capped

    for p in picks:
        log.info(f"  BUY {p['outcome']:>3} @ {p['ask']:.3f} ({1/p['ask']:.2f} dec) "
                 f"depth ${p['depth']:>7,.0f}  {p['question'][:52]}")

    if args.dry_run or not picks:
        if args.dry_run:
            log.info("dry-run — nothing written")
        return

    with _conn() as conn, conn.cursor() as cur:
        sid = _strategy_id(cur)
        written = 0
        for p in picks:
            # One position per token, ever — the hypothesis is one entry per market.
            cur.execute("SELECT 1 FROM paper_trades WHERE pm_token_id = %s", (p["token_id"],))
            if cur.fetchone():
                continue
            # Link a pm_markets row so the public dashboard can show a real
            # title — it renders paper_trades joined to pm_markets, and a NULL
            # market_id shows up as a bare em-dash.
            market_db_id = _upsert_pm_market(
                conn, p["condition_id"], p["question"], p["kickoff"].isoformat())

            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                                          stake_units, model_probability, expected_edge,
                                          reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,now())
            """, (sid, market_db_id, p["outcome"], p["ask"], 1.0 / p["ask"],
                  STAKE_UNITS, None, None,
                  # cond= is parsed by flb_eval to resolve via CLOB /markets/<cid>;
                  # paper_trades has no condition_id column and Gamma has no
                  # working token filter, so this string is the only link back.
                  f"H-FLB id=25 | {p['event_title']} | {p['question']} | "
                  f"ask={p['ask']:.3f} bid={p['bid']:.3f} depth=${p['depth']:.0f} | "
                  f"ko={p['kickoff'].isoformat()} | cond={p['condition_id']}",
                  p["token_id"]))
            written += 1
        conn.commit()
    log.info(f"wrote {written} paper positions (strategy '{STRATEGY_NAME}')")


if __name__ == "__main__":
    main()
