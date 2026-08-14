#!/usr/bin/env python3
"""
PM vs Pinnacle — is Polymarket's 1X2 price different from the sharp line?

Pulls Pinnacle's pre-match 1X2 from api-football for every fixture on a date,
pulls Polymarket's executable ask for the same fixture from the CLOB, and
measures the gap.

Three things this is careful about, because each one has produced a false
positive in this project before:

1. THE PRICE IS THE ASK, NOT THE MID. A Gamma mid is not tradeable. The whole
   pre-match FLB result flipped on this: the same trades ran -4.6% at the ask,
   -2.7% at the mid and -0.8% at the bid, and the loss WAS the spread.

2. THE DE-VIG METHOD IS A SUSPECT. Proportional de-vig assumes the bookmaker
   spreads margin evenly across outcomes, which is known to be false — it
   over-states the fair probability of longshots. If a "discrepancy" lives
   mostly in away/draw longshots, the method is a likelier explanation than the
   market. So both proportional and power de-vig are computed, and the report
   splits by outcome and by price band. A gap that survives both is interesting;
   a gap that only exists under proportional is an artifact.

3. A SNAPSHOT GAP IS NOT AN EDGE. This measures a difference at one instant. It
   becomes an edge only if it predicts the result or the close, after costs.
   The report subtracts the taker fee and states the round-trip friction, and
   --record persists rows so the question can be answered later instead of
   argued now.

Usage:
    python pm_vs_pinnacle.py                  # today + tomorrow, report only
    python pm_vs_pinnacle.py --days 3
    python pm_vs_pinnacle.py --record         # also persist rows for validation
    python pm_vs_pinnacle.py --verbose        # per-fixture detail
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

from edge_engine import taker_fee_pp                       # noqa: E402
from late_goals_observer import _fetch_book, _is_football   # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S", force=True)
log = logging.getLogger("pm_v_pin")

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
AF_API = "https://v3.football.api-sports.io"
GAMMA_API = "https://gamma-api.polymarket.com"
PINNACLE_BOOKMAKER_ID = 4

MAX_WORKERS = 16
# Above this ask PM's own settlement calibration is broken — asks around 0.955
# resolved at 0.660 on n=382. A "discrepancy" up there measures that, not a line.
MAX_TRUSTWORTHY_ASK = 0.85


# ── de-vig ───────────────────────────────────────────────────────────────────

def devig_proportional(odds: dict[str, float]) -> dict[str, float]:
    """Scale implied probabilities to sum to 1. The house convention, and the
    one that over-states longshots."""
    imp = {k: 1.0 / v for k, v in odds.items()}
    total = sum(imp.values())
    return {k: v / total for k, v in imp.items()}


def devig_power(odds: dict[str, float], tol: float = 1e-10) -> dict[str, float]:
    """Solve for k with sum(p_i^k) = 1, where p_i is the raw implied probability.

    Power de-vig takes proportionally more margin out of longshots than out of
    favourites, which is closer to how books actually price. Where the two
    methods disagree materially, the outcome is a longshot and neither number
    should be trusted far.
    """
    imp = [1.0 / v for v in odds.values()]
    lo, hi = 0.5, 2.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(p ** k for p in imp)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return {name: (1.0 / o) ** k for name, o in odds.items()}


# ── api-football: Pinnacle by date ───────────────────────────────────────────

def fetch_pinnacle_for_date(date_str: str) -> dict[int, dict[str, float]]:
    """{fixture_id: {'home': odd, 'draw': odd, 'away': odd}} for one date."""
    out: dict[int, dict[str, float]] = {}
    page = 1
    while True:
        try:
            resp = requests.get(
                f"{AF_API}/odds",
                params={"date": date_str, "bookmaker": PINNACLE_BOOKMAKER_ID, "page": page},
                headers={"x-apisports-key": FOOTBALL_API_KEY},
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
        except Exception as exc:
            log.warning(f"pinnacle {date_str} page {page}: {exc}")
            break

        for item in data.get("response", []):
            fid = (item.get("fixture") or {}).get("id")
            if not fid:
                continue
            for bm in item.get("bookmakers", []):
                if bm.get("id") != PINNACLE_BOOKMAKER_ID:
                    continue
                for bet in bm.get("bets", []):
                    if bet.get("id") != 1:          # Match Winner
                        continue
                    odds = {}
                    for v in bet.get("values", []):
                        key = str(v.get("value", "")).lower()
                        try:
                            o = float(v.get("odd", 0))
                        except (TypeError, ValueError):
                            continue
                        if o > 1.0 and key in ("home", "draw", "away"):
                            odds[key] = o
                    if len(odds) == 3:
                        out[fid] = odds

        paging = data.get("paging") or {}
        if page >= int(paging.get("total") or 1):
            break
        page += 1
    return out


def fetch_fixtures_for_date(date_str: str) -> dict[int, dict]:
    """{fixture_id: {home, away, league, kickoff}} — needed to name the odds."""
    try:
        resp = requests.get(
            f"{AF_API}/fixtures", params={"date": date_str},
            headers={"x-apisports-key": FOOTBALL_API_KEY}, timeout=25,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception as exc:
        log.warning(f"fixtures {date_str}: {exc}")
        return {}

    out = {}
    for f in data.get("response", []):
        fid = (f.get("fixture") or {}).get("id")
        teams = f.get("teams") or {}
        if not fid:
            continue
        out[fid] = {
            "home": (teams.get("home") or {}).get("name", ""),
            "away": (teams.get("away") or {}).get("name", ""),
            "league": (f.get("league") or {}).get("name", ""),
            "kickoff": (f.get("fixture") or {}).get("date", ""),
        }
    return out


# ── Polymarket: 1X2 with executable books ────────────────────────────────────

_SUFFIX_RE = re.compile(r"\s+-\s+[A-Z][A-Za-z0-9 /'&.]*$")


def fetch_pm_fixtures(days: int) -> list[dict]:
    """PM football fixtures kicking off within `days`, with their 1X2 markets."""
    now = datetime.now(timezone.utc)
    date_min = now.strftime("%Y-%m-%d")
    date_max = (now + timedelta(days=days)).strftime("%Y-%m-%d")

    events: list[dict] = []
    for offset in range(0, 3000, 100):
        try:
            resp = requests.get(f"{GAMMA_API}/events", params={
                "closed": "false", "active": "true", "limit": 100, "offset": offset,
                "end_date_min": date_min, "end_date_max": date_max,
            }, timeout=20)
            resp.raise_for_status()
            page = resp.json()
        except Exception as exc:
            log.warning(f"gamma: {exc}")
            break
        if not isinstance(page, list) or not page:
            break
        events.extend(page)

    fixtures: dict[str, dict] = {}
    for ev in events:
        title = ev.get("title", "")
        if " vs" not in title or not _is_football(ev):
            continue
        base = _SUFFIX_RE.sub("", title).strip()
        fx = fixtures.setdefault(base, {
            "title": base,
            "kickoff": ev.get("startTime"),
            "markets": [],
        })
        fx["markets"].extend(ev.get("markets") or [])
    return list(fixtures.values())


def pm_1x2(fixture: dict) -> dict[str, dict] | None:
    """{'home'|'draw'|'away': {token_id, gamma_mid}} from PM's three binaries.

    PM splits 1X2 into "Will <team> win?", "Will <team> win?" and "Will ... end
    in a draw?", each with Yes/No outcomes. The team is in the QUESTION, never
    in the outcome name.
    """
    home, away = [p.strip() for p in re.split(r" vs\.? ", fixture["title"], maxsplit=1)] \
        if " vs" in fixture["title"] else (None, None)
    if not home or not away:
        return None

    found: dict[str, dict] = {}
    for mkt in fixture["markets"]:
        q = str(mkt.get("question") or "")
        if mkt.get("closed") or "win" not in q.lower() and "draw" not in q.lower():
            continue
        # Sub-markets ("win by 2+", "win either half", "1st half") are not 1X2.
        if re.search(r"by \d|either half|1st half|2nd half|half|clean sheet|both", q, re.I):
            continue

        side = None
        if re.search(r"end in a draw|\bdraw\b", q, re.I):
            side = "draw"
        elif home.lower()[:9] in q.lower():
            side = "home"
        elif away.lower()[:9] in q.lower():
            side = "away"
        if side is None or side in found:
            continue

        try:
            import json
            prices = json.loads(mkt.get("outcomePrices") or "[]")
            names = json.loads(mkt.get("outcomes") or "[]")
            tokens = json.loads(mkt.get("clobTokenIds") or "[]")
        except Exception:
            continue

        for i, name in enumerate(names):
            if str(name).strip().lower() != "yes":
                continue
            if i < len(tokens):
                found[side] = {
                    "token_id": tokens[i],
                    "gamma_mid": float(prices[i]) if i < len(prices) else None,
                    "question": q,
                }
    return found if len(found) == 3 else None


# ── matching ─────────────────────────────────────────────────────────────────

# Matching lives in fixture_match — see the module docstring for the two
# wrong-fixture comparisons the naive version produced here.
from fixture_match import best_match                          # noqa: E402


# ── scan ─────────────────────────────────────────────────────────────────────

def scan(days: int, verbose: bool) -> list[dict]:
    dates = [(datetime.now(timezone.utc) + timedelta(days=d)).strftime("%Y-%m-%d")
             for d in range(days)]

    af_fixtures: dict[int, dict] = {}
    pinnacle: dict[int, dict[str, float]] = {}
    for d in dates:
        af_fixtures.update(fetch_fixtures_for_date(d))
        got = fetch_pinnacle_for_date(d)
        pinnacle.update(got)
        log.info(f"{d}: {len(got)} fixtures with a Pinnacle 1X2")

    pm_fixtures = fetch_pm_fixtures(days)
    log.info(f"PM: {len(pm_fixtures)} football fixtures in the window")

    # Resolve which PM fixtures have both a Pinnacle line and a full 1X2 board,
    # then fetch every needed CLOB book in one parallel pass.
    pending: list[tuple[dict, dict, dict, dict]] = []
    for fx in pm_fixtures:
        sides = pm_1x2(fx)
        if not sides:
            continue
        fid = best_match(fx["title"], fx.get("kickoff"), af_fixtures)
        if fid is None or fid not in pinnacle:
            continue
        pending.append((fx, sides, pinnacle[fid], af_fixtures[fid]))

    log.info(f"matched on both venues with a full 1X2: {len(pending)} fixtures")

    tokens = {s["token_id"]: s for _, sides, _, _ in pending for s in sides.values()}
    books: dict[str, dict] = {}
    if tokens:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for tok, book in zip(tokens, pool.map(
                    lambda t: _fetch_book({"token_id": t}), tokens)):
                if book:
                    books[tok] = book

    rows: list[dict] = []
    for fx, sides, odds, info in pending:
        prop = devig_proportional(odds)
        power = devig_power(odds)
        overround = 100.0 * (sum(1.0 / o for o in odds.values()) - 1.0)

        for side in ("home", "draw", "away"):
            book = books.get(sides[side]["token_id"])
            if not book or book.get("best_ask") is None:
                continue
            ask = book["best_ask"]
            fee = taker_fee_pp(ask)
            rows.append({
                "fixture": fx["title"],
                # The fixture as api-football names it, carried through so an
                # outlier can be audited without re-deriving the join. Every
                # large "edge" this scan has produced so far was a wrong
                # pairing, and reading the two names side by side is the fastest
                # way to see it.
                "af_fixture": f"{info['home']} v {info['away']}",
                "league": info["league"],
                "kickoff": info["kickoff"],
                "side": side,
                "pin_odds": odds[side],
                "pin_prop": prop[side],
                "pin_power": power[side],
                "pin_overround_pp": overround,
                "pm_ask": ask,
                "pm_bid": book.get("best_bid"),
                "pm_mid": sides[side]["gamma_mid"],
                "pm_spread_pp": (100.0 * (ask - book["best_bid"]))
                                if book.get("best_bid") is not None else None,
                "ask_depth_usd": book.get("ask_depth_usd"),
                "fee_pp": fee,
                "edge_prop_pp": 100.0 * (prop[side] - ask) - fee,
                "edge_power_pp": 100.0 * (power[side] - ask) - fee,
            })

    if verbose:
        for r in sorted(rows, key=lambda x: -x["edge_prop_pp"])[:25]:
            log.info(
                f"  {r['fixture'][:34]:34} {r['side']:5} "
                f"pin={1 / r['pin_prop']:5.2f} pm={1 / r['pm_ask']:5.2f} "
                f"edge={r['edge_prop_pp']:+6.1f}pp (power {r['edge_power_pp']:+6.1f}) "
                f"spread={r['pm_spread_pp'] or 0:.1f}pp"
            )
    return rows


# ── report ───────────────────────────────────────────────────────────────────

def _ci(vals: list[float]) -> tuple[float, float, float]:
    """(mean, lo, hi) at 95%. Normal approximation is fine at these n."""
    if len(vals) < 2:
        return (vals[0] if vals else 0.0, float("nan"), float("nan"))
    m = statistics.fmean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals))
    return m, m - 1.96 * se, m + 1.96 * se


def report(rows: list[dict]) -> None:
    if not rows:
        print("\nNo fixture was quoted on both venues with a full 1X2 and a live book.")
        return

    trusted = [r for r in rows if r["pm_ask"] <= MAX_TRUSTWORTHY_ASK]

    print(f"\n{'=' * 78}")
    print(f"PM vs PINNACLE — {len(rows)} outcomes across "
          f"{len({r['fixture'] for r in rows})} fixtures")
    print(f"{'=' * 78}")

    m, lo, hi = _ci([r["edge_prop_pp"] for r in trusted])
    mp, lop, hip = _ci([r["edge_power_pp"] for r in trusted])
    print(f"\nMean edge, ask vs de-vigged Pinnacle, after the taker fee "
          f"(n={len(trusted)}, ask <= {MAX_TRUSTWORTHY_ASK}):")
    print(f"  proportional de-vig  {m:+6.2f}pp  CI[{lo:+.2f}, {hi:+.2f}]")
    print(f"  power de-vig         {mp:+6.2f}pp  CI[{lop:+.2f}, {hip:+.2f}]")
    print("  A negative mean means PM is EXPENSIVE against the sharp line — "
          "which is the null.")

    # The number above conflates two different claims: how PM's PRICE sits
    # against the sharp line, and what it costs US to take it. Decomposed, the
    # first is a statement about the venue and the second is a statement about
    # our execution, and they have different remedies.
    gross = [r["edge_prop_pp"] + r["fee_pp"] for r in trusted]
    gm, glo, ghi = _ci(gross)
    mid_gap = [100.0 * (r["pin_prop"] - r["pm_mid"])
               for r in trusted if r["pm_mid"] is not None]
    fees = [r["fee_pp"] for r in trusted]
    print(f"\nDecomposed:")
    print(f"  ask vs sharp, BEFORE fee   {gm:+6.2f}pp  CI[{glo:+.2f}, {ghi:+.2f}]")
    print(f"  taker fee                  {-statistics.fmean(fees):+6.2f}pp")
    if mid_gap:
        mm, mlo, mhi = _ci(mid_gap)
        print(f"  MID vs sharp               {mm:+6.2f}pp  CI[{mlo:+.2f}, {mhi:+.2f}]")
        print("  If the mid sits on the sharp line and the ask does not, the gap "
              "is the spread — a cost, not a mispricing.")

    spreads = [r["pm_spread_pp"] for r in trusted if r["pm_spread_pp"] is not None]
    if spreads:
        print(f"\nPM spread: median {statistics.median(spreads):.2f}pp, "
              f"mean {statistics.fmean(spreads):.2f}pp  "
              f"(round trip is roughly double that)")
    # Pinnacle's 1X2 overround is normally 2.5-3.5pp on major leagues. Much wider
    # than that means either the feed is not really Pinnacle's sharp line, or
    # these are minor competitions where even Pinnacle runs a fat margin — and a
    # fat-margin line is a worse fair-value oracle, which changes what a gap
    # against it is worth.
    orr = sorted(r["pin_overround_pp"] for r in trusted)
    if orr:
        print(f"Pinnacle overround: median {statistics.median(orr):.2f}pp, "
              f"p10 {orr[len(orr) // 10]:.2f}pp, p90 {orr[-max(1, len(orr) // 10)]:.2f}pp")
        # The decisive cut. A 12pp-overround line on a Swedish fourth division is
        # a poor fair-value oracle no matter whose name is on it, so the mid-vs-
        # sharp comparison is repeated by margin band. If PM's mid tracks the
        # sharp line on the TIGHT lines too, the agreement is real; if it only
        # holds where the line is fat, it is de-vig slack rather than agreement.
        print("\nMID vs sharp, by how sharp the Pinnacle line actually is:")
        for lo_o, hi_o, label in [(0.0, 4.5, "tight  <=4.5pp"),
                                  (4.5, 7.0, "medium 4.5-7pp"),
                                  (7.0, 99.0, "fat     >7pp")]:
            sub = [r for r in trusted
                   if lo_o <= r["pin_overround_pp"] < hi_o and r["pm_mid"] is not None]
            if len(sub) < 10:
                continue
            g, glo2, ghi2 = _ci([100.0 * (r["pin_prop"] - r["pm_mid"]) for r in sub])
            e, elo, ehi = _ci([r["edge_prop_pp"] for r in sub])
            print(f"  {label:16} n={len(sub):4d}  mid gap {g:+6.2f}pp "
                  f"CI[{glo2:+.2f},{ghi2:+.2f}]   after fee+spread {e:+6.2f}pp "
                  f"CI[{elo:+.2f},{ehi:+.2f}]")

    print("\nBy outcome:")
    by_side: dict[str, list[dict]] = defaultdict(list)
    for r in trusted:
        by_side[r["side"]].append(r)
    for side in ("home", "draw", "away"):
        sub = by_side.get(side) or []
        if not sub:
            continue
        m1, l1, h1 = _ci([r["edge_prop_pp"] for r in sub])
        m2, _, _ = _ci([r["edge_power_pp"] for r in sub])
        print(f"  {side:5} n={len(sub):4d}  prop {m1:+6.2f}pp CI[{l1:+.2f},{h1:+.2f}]"
              f"   power {m2:+6.2f}pp")

    print("\nBy PM ask band:")
    bands = [(0.0, 0.15), (0.15, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.85)]
    for lo_b, hi_b in bands:
        sub = [r for r in trusted if lo_b <= r["pm_ask"] < hi_b]
        if len(sub) < 5:
            continue
        m1, l1, h1 = _ci([r["edge_prop_pp"] for r in sub])
        m2, _, _ = _ci([r["edge_power_pp"] for r in sub])
        print(f"  {lo_b:.2f}-{hi_b:.2f} ({1 / hi_b:4.2f}-{1 / max(lo_b, 0.01):5.2f})  "
              f"n={len(sub):4d}  prop {m1:+6.2f}pp CI[{l1:+.2f},{h1:+.2f}]"
              f"   power {m2:+6.2f}pp")

    # The only rows that would actually be actionable.
    live = [r for r in trusted
            if r["edge_prop_pp"] >= 2.0 and r["edge_power_pp"] >= 2.0
            and (r["ask_depth_usd"] or 0) >= 50]
    print(f"\nOutcomes clearing +2pp under BOTH de-vig methods with $50+ depth: "
          f"{len(live)} of {len(trusted)}")
    if live:
        print("  (check the two names agree BEFORE reading the number — every "
              "large gap this scan has produced was a wrong pairing)")
    for r in sorted(live, key=lambda x: -x["edge_prop_pp"])[:15]:
        print(f"  {r['fixture'][:34]:34} {r['side']:5} "
              f"pin {1 / r['pin_prop']:5.2f}  pm {1 / r['pm_ask']:5.2f}  "
              f"{r['edge_prop_pp']:+5.1f}pp / {r['edge_power_pp']:+5.1f}pp  "
              f"${r['ask_depth_usd']:.0f}")
        print(f"      af: {r['af_fixture'][:60]}  [{r['league'][:24]}]")

    excluded = len(rows) - len(trusted)
    if excluded:
        print(f"\n{excluded} outcomes excluded at ask > {MAX_TRUSTWORTHY_ASK} — "
              f"PM asks up there resolved at 0.66 on n=382, so a gap there "
              f"measures broken calibration, not a line.")
    print("\nThis is a snapshot difference, not an edge. It becomes an edge only "
          "if it predicts the close or the result AFTER the round-trip cost above.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare PM 1X2 asks against Pinnacle")
    ap.add_argument("--days", type=int, default=2, help="days ahead to scan")
    ap.add_argument("--verbose", action="store_true", help="per-outcome detail")
    args = ap.parse_args()

    if not FOOTBALL_API_KEY:
        raise SystemExit("FOOTBALL_API_KEY not set")

    report(scan(args.days, args.verbose))


if __name__ == "__main__":
    main()
