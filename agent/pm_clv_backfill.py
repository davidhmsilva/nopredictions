"""
pm_clv_backfill.py — Polymarket closing-line value (PM-CLV).

Answers the one question the existing CLV framework can't: *did the price we paid
on Polymarket beat Polymarket's OWN closing price?*  `paper_trades.clv` measures
CLV vs the sharp line (Pinnacle/Betfair, see 010_clv_source.sql) — but we trade on
PM, so PM's own close is the most direct edge benchmark for our entries.

For every settled trade missing pm_clv, it finds the PM price of the bought token
at/just before kickoff from two sources, in priority:

  1. 'observed'     — the last pre-kickoff snapshot the observer already recorded
                      for that token in market_observations. Free, fine-grained,
                      in our own DB. (Matches YES bets, where our bought token ==
                      the YES token the observer stores.)
  2. 'clob_history' — Polymarket CLOB /prices-history for the bought token, last
                      point with t <= kickoff. Universal (YES and NO), and backfills
                      the period before the observer existed. Coarse for resolved
                      markets (PM serves >=12h granularity once a market settles) —
                      pm_closing_at records exactly how stale the point used is.

  pm_clv = entry_odds * pm_closing_price - 1   (same units as clv; on the bought
  token, so correct for YES and NO).

This is wired into observer.py (best-effort, every run) and also runs standalone:

  cd agent && source ../ingest/.venv/bin/activate
  python pm_clv_backfill.py --dry-run      # compute + print, no DB writes
  python pm_clv_backfill.py                # write pm_clv to settled trades
  python pm_clv_backfill.py --limit 100    # cap how many trades to process
  python pm_clv_backfill.py --report       # summary of PM-CLV collected so far
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pm_clv] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pm_clv")

DATABASE_URL = os.getenv("DATABASE_URL")
CLOB_API = os.getenv("POLYMARKET_CLOB_API", "https://clob.polymarket.com").rstrip("/")
GAMMA_API = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/")

# Real PM closing-line value almost never exceeds this in absolute terms; beyond it
# is almost always a token/outcome-mapping artifact, not edge. More permissive than
# the sharp-CLV cap (0.5 in resolver.py) because PM longshots genuinely move a lot.
PM_MAX_PLAUSIBLE_CLV = 1.0

_gamma_cache: dict[str, dict | None] = {}


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _is_no_bet(outcome: str | None) -> bool:
    ol = (outcome or "").lower()
    return ol.startswith("not ") or ol.startswith("no ")


# ─── Sources of the PM closing price ─────────────────────────────────────────

def _closing_from_observations(cur, token_id: str | None) -> tuple[float | None, datetime | None, datetime | None]:
    """
    Last pre-kickoff observer snapshot for this exact (bought) token.
    Returns (closing_price, closing_at, kickoff_utc). Only matches when the bought
    token == the YES token the observer stored — i.e. YES bets.
    """
    if not token_id:
        return None, None, None
    cur.execute(
        """
        SELECT pm_yes, observed_at, kickoff_utc
        FROM market_observations
        WHERE pm_token_id = %s
          AND phase = 'pre'
          AND kickoff_utc IS NOT NULL
          AND observed_at <= kickoff_utc
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (token_id,),
    )
    row = cur.fetchone()
    if not row:
        return None, None, None
    return _f(row["pm_yes"]), row["observed_at"], row["kickoff_utc"]


def _kickoff_from_observations(cur, external_id: str | None) -> datetime | None:
    if not external_id:
        return None
    cur.execute(
        """
        SELECT kickoff_utc FROM market_observations
        WHERE pm_external_id = %s AND kickoff_utc IS NOT NULL
        ORDER BY observed_at DESC LIMIT 1
        """,
        (external_id,),
    )
    row = cur.fetchone()
    return row["kickoff_utc"] if row else None


def _gamma_market(external_id: str | None) -> dict | None:
    """Fetch a Gamma market (numeric id) for gameStartTime + clobTokenIds fallback."""
    if not external_id or external_id.startswith("0x"):
        return None  # 0x conditionIds aren't Gamma-addressable; rely on stored token
    if external_id in _gamma_cache:
        return _gamma_cache[external_id]
    out = None
    try:
        r = requests.get(f"{GAMMA_API}/markets/{external_id}", timeout=15)
        if r.ok:
            out = r.json()
    except requests.RequestException:
        out = None
    _gamma_cache[external_id] = out
    return out


def _gamma_kickoff(mkt: dict | None) -> datetime | None:
    if not mkt:
        return None
    for key in ("gameStartTime", "startDate", "endDate"):
        v = mkt.get(key)
        if not v:
            continue
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _gamma_token(mkt: dict | None, is_no: bool) -> str | None:
    if not mkt:
        return None
    raw = mkt.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    return str(raw[1] if is_no else raw[0])


def _clob_prices_history(token_id: str) -> list[tuple[int, float]]:
    """Full price history for a token via the public CLOB endpoint. [(unix_ts, price)]."""
    try:
        r = requests.get(
            f"{CLOB_API}/prices-history",
            params={"market": token_id, "interval": "max"},
            timeout=20,
        )
        if not r.ok:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for pt in data.get("history") or []:
        t, p = pt.get("t"), _f(pt.get("p"))
        if t is None or p is None:
            continue
        out.append((int(t), p))
    out.sort()
    return out


def _closing_from_clob(token_id: str | None, kickoff: datetime) -> tuple[float | None, datetime | None]:
    """PM closing price = last /prices-history point at/just before kickoff."""
    if not token_id:
        return None, None
    pts = _clob_prices_history(token_id)
    if not pts:
        return None, None
    ko_ts = _to_unix(kickoff)
    before = [(t, p) for t, p in pts if t <= ko_ts]
    if not before:
        return None, None
    t, p = before[-1]
    return p, datetime.fromtimestamp(t, tz=timezone.utc)


# ─── Per-trade resolution ────────────────────────────────────────────────────

def _resolve_trade(cur, trade: dict) -> dict:
    """
    Compute PM-CLV for one trade. ALWAYS returns a dict to persist, tagged by
    pm_clv_source, so each trade (and its network calls) is processed at most once:
      observed | clob_history — real PM-CLV found
      suspect                 — closing price found but |clv| implausibly large
      inplay                  — entered after kickoff; closing-line CLV doesn't apply
      unavailable             — no usable PM closing price (pre-observer / PM dropped it)
    """
    base = {"id": trade["id"], "pm_closing_price": None, "pm_closing_at": None,
            "pm_clv": None, "pm_clv_source": None}

    entry_price = _f(trade["entry_price"])
    entry_odds = _f(trade["entry_odds"]) or (1.0 / entry_price if entry_price else None)
    if not entry_odds:
        return {**base, "pm_clv_source": "unavailable"}

    token = trade.get("pm_token_id")
    external_id = trade.get("external_id")
    is_no = _is_no_bet(trade.get("outcome"))

    closing_price = closing_at = kickoff = None
    source = None

    # Source 1: observer snapshot for the bought token (YES bets) — free, precise.
    cp, cat, ko = _closing_from_observations(cur, token)
    if cp is not None and 0.0 < cp < 1.0:
        closing_price, closing_at, kickoff, source = cp, cat, ko, "observed"

    # Source 2: CLOB /prices-history for the bought token (universal, backfill).
    if source is None:
        kickoff = _kickoff_from_observations(cur, external_id)
        gm = None
        if kickoff is None or token is None:
            gm = _gamma_market(external_id)
        if kickoff is None:
            kickoff = _gamma_kickoff(gm)
        if kickoff is None and trade.get("resolution_time"):
            # last resort: football kickoff ~2h before market resolution
            kickoff = trade["resolution_time"] - timedelta(hours=2)
        if token is None:
            token = _gamma_token(gm, is_no)
        if kickoff is not None and token:
            cp, cat = _closing_from_clob(token, kickoff)
            if cp is not None and 0.0 < cp < 1.0:
                closing_price, closing_at, source = cp, cat, "clob_history"

    if closing_price is None:
        return {**base, "pm_clv_source": "unavailable"}

    # We have a price. In-play entries compare a post-kickoff entry against the
    # pre-kickoff close — that is not closing-line value, so tag and skip rather
    # than record a misleading CLV. (Only meaningful once we actually have a price.)
    entry_at = trade.get("entry_at")
    if entry_at and kickoff and entry_at > kickoff + timedelta(minutes=5):
        return {**base, "pm_clv_source": "inplay"}

    clv = round(entry_odds * closing_price - 1.0, 4)
    if abs(clv) > PM_MAX_PLAUSIBLE_CLV:
        # plausible price but an implausible CLV -> keep the price, flag, drop clv
        return {**base, "pm_closing_price": round(closing_price, 6),
                "pm_closing_at": closing_at, "pm_clv_source": "suspect"}

    return {"id": trade["id"], "pm_closing_price": round(closing_price, 6),
            "pm_closing_at": closing_at, "pm_clv": clv, "pm_clv_source": source}


# ─── Batch backfill ──────────────────────────────────────────────────────────

def backfill(limit: int = 500, dry_run: bool = False, quiet: bool = False) -> dict:
    conn = _conn()
    updated = 0
    by_source: dict[str, int] = {}
    samples = []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT pt.id, pt.outcome, pt.entry_price, pt.entry_odds, pt.pm_token_id,
                   pt.pm_live, pt.clv,
                   COALESCE(pt.pm_executed_at, pt.placed_at) AS entry_at,
                   pm.external_id, pm.resolution_time
            FROM paper_trades pt
            LEFT JOIN pm_markets pm ON pm.id = pt.market_id
            WHERE pt.result IN ('won', 'lost', 'void')
              AND pt.pm_clv_source IS NULL
              AND pt.entry_price IS NOT NULL
            ORDER BY pt.placed_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        trades = [dict(r) for r in cur.fetchall()]
        if not quiet:
            log.info(f"{len(trades)} untagged settled trade(s)")

        wcur = conn.cursor()
        for t in trades:
            res = _resolve_trade(cur, t)
            by_source[res["pm_clv_source"]] = by_source.get(res["pm_clv_source"], 0) + 1
            if res["pm_clv"] is not None and len(samples) < 25:
                samples.append((t, res))
            if not dry_run:
                wcur.execute(
                    """UPDATE paper_trades
                       SET pm_closing_price=%s, pm_closing_at=%s,
                           pm_clv=%s, pm_clv_source=%s
                       WHERE id=%s""",
                    (res["pm_closing_price"], res["pm_closing_at"],
                     res["pm_clv"], res["pm_clv_source"], res["id"]),
                )
                updated += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    resolved = by_source.get("observed", 0) + by_source.get("clob_history", 0)

    if dry_run and not quiet:
        for t, res in sorted(samples, key=lambda x: -(abs(x[1]["pm_clv"] or 0))):
            tag = "LIVE" if t.get("pm_live") else "papr"
            clv = res["pm_clv"]
            clv_s = f"{clv:+.3f}" if clv is not None else "  n/a"
            log.info(
                f"  #{t['id']:>5} [{tag}] {(t['outcome'] or '')[:22]:22} "
                f"entry {float(t['entry_price']):.3f} → PM close {res['pm_closing_price']:.3f}"
                f" | PM-CLV {clv_s} ({res['pm_clv_source']})"
            )
        n = sum(by_source.values())
        log.info(f"[dry-run] {n} processed ({dict(by_source)}); 0 written")
    elif not quiet:
        log.info(f"Tagged {updated} trade(s); {resolved} with real PM-CLV {dict(by_source)}")

    return {"updated": updated, "resolved": resolved, "by_source": by_source}


def report() -> None:
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE result IN ('won','lost','void'))            AS settled,
            COUNT(pm_clv)                                                      AS with_pm_clv,
            COUNT(*) FILTER (WHERE pm_clv_source='suspect')                    AS suspect,
            ROUND(AVG(pm_clv)::numeric, 4)                                     AS avg_pm_clv,
            ROUND(AVG(pm_clv) FILTER (WHERE pm_live)::numeric, 4)              AS avg_pm_clv_live,
            ROUND(AVG(clv)::numeric, 4)                                        AS avg_sharp_clv,
            ROUND(AVG(pm_clv) FILTER (WHERE pm_clv > 0)::numeric, 4)           AS avg_pos,
            COUNT(*) FILTER (WHERE pm_clv > 0)                                 AS n_pos,
            COUNT(*) FILTER (WHERE pm_clv < 0)                                 AS n_neg
        FROM paper_trades
        """
    )
    s = dict(cur.fetchone())
    print(f"\nsettled trades        : {s['settled']}")
    print(f"with PM-CLV           : {s['with_pm_clv']}   (suspect/flagged: {s['suspect']})")
    print(f"avg PM-CLV            : {s['avg_pm_clv']}   (live only: {s['avg_pm_clv_live']})")
    print(f"  beat PM close       : {s['n_pos']} pos / {s['n_neg']} neg   "
          f"(avg when positive {s['avg_pos']})")
    print(f"avg sharp CLV (clv)   : {s['avg_sharp_clv']}   ← for comparison")

    cur.execute(
        """SELECT pm_clv_source, COUNT(*) n, ROUND(AVG(pm_clv)::numeric,4) avg
           FROM paper_trades WHERE pm_clv_source IS NOT NULL
           GROUP BY pm_clv_source ORDER BY n DESC"""
    )
    rows = cur.fetchall()
    if rows:
        print("\nby source:")
        for r in rows:
            print(f"  {r['pm_clv_source']:13} n={r['n']:>4}  avg PM-CLV {r['avg']}")

    cur.execute(
        """SELECT st.name, COUNT(*) n, ROUND(AVG(pt.pm_clv)::numeric,4) avg
           FROM paper_trades pt JOIN strategies st ON st.id=pt.strategy_id
           WHERE pt.pm_clv IS NOT NULL
           GROUP BY st.name ORDER BY n DESC"""
    )
    rows = cur.fetchall()
    if rows:
        print("\nby strategy (n>=1):")
        for r in rows:
            print(f"  {r['name'][:34]:34} n={r['n']:>4}  avg PM-CLV {r['avg']}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Polymarket closing-line value backfill")
    ap.add_argument("--dry-run", action="store_true", help="compute + print, no DB writes")
    ap.add_argument("--limit", type=int, default=500, help="max trades to process")
    ap.add_argument("--report", action="store_true", help="summary of PM-CLV collected")
    args = ap.parse_args()
    if args.report:
        report()
    else:
        backfill(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
