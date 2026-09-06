"""
wc_overs_live — Strategy B1: in-play OVER 2.5 hunter for the FIFA World Cup.

Entry rule (price-only, no model filter):
  • Both teams in WC nations
  • pm_ask(over_2.5) ≤ WC_OVERS_MAX_PRICE   (default 0.30)
  • current score total ≤ WC_OVERS_MAX_SCORE  (default 1 goal)
  • WC_OVERS_MIN_MINUTE ≤ minute ≤ WC_OVERS_MAX_MINUTE  (default 30–65)
  • not already entered for this match (strategy + match dedup)

Caps:
  • $1 stake (PM_LIVE_STAKE_USD, inherited from live_executor)
  • $2.50 per-order notional cap (PM_MAX_NOTIONAL_PER_ORDER)
  • $10 daily notional cap (WC_DAILY_NOTIONAL_CAP)
  • $50 total cap (WC_TOTAL_CAP) — kill switch once breached

Modes:
  • WC_LIVE_OVERS=0 (default): SHADOW — write paper_trades, skip live executor entirely
  • WC_LIVE_OVERS=1: LIVE — call live_executor.try_execute (which itself respects PM_LIVE_MODE)

Run: `python wc_overs_live.py --once` for one cycle, no args for continuous (60s poll).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "ingest" / ".env")
sys.path.insert(0, str(ROOT / "agent"))

import live_executor  # noqa: E402
from dc_scanner import _norm, _find_team  # noqa: E402
from dixon_coles import DixonColesModel  # noqa: E402
import sim_scanner as ss  # noqa: E402  (for PARAMS_PATH)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wc_overs_live] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wc_overs_live")

DATABASE_URL = os.environ["DATABASE_URL"]
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
GAMMA = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/")

PRICE_MAX = float(os.environ.get("WC_OVERS_MAX_PRICE", "0.30"))
MINUTE_MIN = int(os.environ.get("WC_OVERS_MIN_MINUTE", "30"))
MINUTE_MAX = int(os.environ.get("WC_OVERS_MAX_MINUTE", "65"))
SCORE_MAX = int(os.environ.get("WC_OVERS_MAX_SCORE", "1"))

DAILY_CAP_USD = float(os.environ.get("WC_DAILY_NOTIONAL_CAP", "10.0"))
TOTAL_CAP_USD = float(os.environ.get("WC_TOTAL_CAP", "50.0"))

LIVE_ENABLED = os.environ.get("WC_LIVE_OVERS", "0") == "1"
POLL_INTERVAL = int(os.environ.get("WC_OVERS_POLL_S", "60"))

STAKE_UNITS = float(os.environ.get("WC_OVERS_STAKE_UNITS", "1.0"))
STRATEGY_NAME = "WC In-Play Overs"

WC_TEAMS = {
    "Argentina", "Brazil", "Mexico", "Canada", "Qatar", "Switzerland",
    "Bosnia and Herzegovina", "Bosnia-Herzegovina", "Bosnia & Herzegovina",
    "Bosnia", "Croatia", "Panama",
    "Portugal", "South Africa", "Czechia", "Korea Republic", "South Korea",
    "Senegal", "Cameroon", "Morocco", "Saudi Arabia", "Jordan", "Australia",
    "IR Iran", "Iran", "Japan", "Egypt", "Ghana", "Algeria", "Tunisia",
    "Ecuador", "Chile", "Peru", "Uruguay", "Colombia", "United States", "USA",
    "France", "Germany", "Spain", "England", "Italy", "Belgium", "Netherlands",
    "Denmark", "Sweden", "Norway", "Poland", "Serbia", "DR Congo",
    "Ivory Coast", "Côte d'Ivoire", "Nigeria", "Costa Rica", "Honduras",
    "Jamaica", "New Zealand", "Wales", "Scotland", "Turkey", "Türkiye",
    "Greece", "Austria", "Hungary", "Romania", "Ukraine", "Mali", "Iraq",
    "UAE", "Uzbekistan", "Bolivia", "Venezuela", "Paraguay", "Slovakia",
    "Slovenia", "Cape Verde", "Cabo Verde", "Haiti", "Curaçao", "Curacao",
}


# ── DB plumbing ───────────────────────────────────────────────────────────────


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _get_or_create_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = %s", (STRATEGY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    # Create a backing hypothesis row (strategies.hypothesis_id is NOT NULL).
    cur.execute(
        """
        INSERT INTO research_hypotheses
            (title, description, source, status, created_at, created_by)
        VALUES (%s, %s, 'manual', 'promoted', NOW(), 'wc_overs_live')
        RETURNING id
        """,
        (
            "WC In-Play Overs B1",
            "In-play over_2.5 on FIFA World Cup matches when PM ask ≤ 0.30, "
            "score total ≤ 1 goal, minute ∈ [30, 65]. Price-only entry, "
            "no model edge filter. Backed by n=7 backfill yield +107%.",
        ),
    )
    hyp_id = cur.fetchone()[0]
    rules = json.dumps({
        "type": "wc_inplay_overs_b1",
        "outcome": "over_2_5",
        "max_price": PRICE_MAX,
        "max_score_total": SCORE_MAX,
        "minute_range": [MINUTE_MIN, MINUTE_MAX],
        "wc_only": True,
        "model_filter": False,
    })
    cur.execute(
        """
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, %s, %s::jsonb, NOW()) RETURNING id
        """,
        (hyp_id, STRATEGY_NAME, rules),
    )
    sid = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy {STRATEGY_NAME} id={sid}")
    return sid


def _exposure(conn, strategy_id: int) -> tuple[float, float]:
    """Return (today_notional, total_notional) for this strategy (live trades only)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          COALESCE(SUM(pm_order_size * pm_order_price)
                   FILTER (WHERE pm_executed_at::date = CURRENT_DATE), 0) AS today,
          COALESCE(SUM(pm_order_size * pm_order_price), 0) AS total
        FROM paper_trades
        WHERE strategy_id = %s AND pm_live = TRUE
        """,
        (strategy_id,),
    )
    today, total = cur.fetchone()
    return float(today or 0), float(total or 0)


def _already_entered(conn, strategy_id: int, home: str, away: str, date_str: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM paper_trades pt
        LEFT JOIN pm_markets m ON m.id = pt.market_id
        WHERE pt.strategy_id = %s
          AND pt.placed_at::date = %s::date
          AND (m.title ILIKE %s OR m.title ILIKE %s)
          AND pt.outcome = 'over'
        LIMIT 1
        """,
        (strategy_id, date_str, f"%{home}%{away}%O/U 2.5%", f"%{away}%{home}%O/U 2.5%"),
    )
    return cur.fetchone() is not None


def _upsert_market(conn, ext_id: str, title: str, end_dt: Optional[datetime]) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pm_markets
            (platform, external_id, title, market_type, status,
             created_at_source, resolution_time, ingested_at)
        VALUES ('polymarket', %s, %s, 'over_under_2_5', 'live',
                NOW(), %s, NOW())
        ON CONFLICT (platform, external_id) DO UPDATE
            SET title = EXCLUDED.title,
                resolution_time = COALESCE(EXCLUDED.resolution_time, pm_markets.resolution_time)
        RETURNING id
        """,
        (ext_id, title, end_dt),
    )
    return cur.fetchone()[0]


def _write_trade(
    conn, strategy_id: int, market_db_id: int, entry_price: float,
    minute: int, score: str, reasoning: str
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO paper_trades
            (strategy_id, market_id, outcome, entry_price, entry_odds,
             stake_units, expected_edge, confidence, reasoning, placed_at)
        VALUES (%s, %s, 'over', %s, %s, %s, NULL, 0.5, %s, NOW())
        RETURNING id
        """,
        (
            strategy_id, market_db_id, entry_price,
            round(1 / entry_price, 4) if entry_price > 0 else None,
            STAKE_UNITS,
            f"WC live over @ {minute}' score {score}. {reasoning}",
        ),
    )
    tid = cur.fetchone()[0]
    conn.commit()
    return tid


# ── api-football: live state + fixture event lookup ───────────────────────────


_LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE"}
_WC_LEAGUE_ID = int(os.environ.get("WC_LEAGUE_ID", "1"))  # api-football: WC


def _fetch_live_football() -> list[dict]:
    """
    Return fixtures currently in progress, restricted to the WC league.

    NOTE: api-football's `live=all` filter does NOT include the FIFA World Cup.
    During 22h of polling on 2026-06-18 we caught one obscure USL League Two
    match and missed 4 concurrent WC matches. So we query by league+date and
    filter client-side to live status codes.
    """
    if not FOOTBALL_API_KEY:
        log.error("FOOTBALL_API_KEY missing — cannot run.")
        return []
    today = datetime.now(timezone.utc).date().isoformat()
    season = datetime.now(timezone.utc).year
    try:
        r = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"league": _WC_LEAGUE_ID, "season": season, "date": today},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(f"api-football HTTP {r.status_code}")
            return []
        all_today = r.json().get("response") or []
    except requests.RequestException as exc:
        log.warning(f"api-football error: {exc}")
        return []
    live = [
        f for f in all_today
        if (f.get("fixture", {}).get("status", {}).get("short") in _LIVE_STATUSES)
    ]
    return live


# ── Gamma: find the over_2.5 market for a live fixture ───────────────────────


_pm_event_cache: dict[str, tuple[float, list[dict]]] = {}


def _fetch_wc_events() -> list[dict]:
    """Get all active WC events (cached 5 min)."""
    key = "wc_events"
    now = time.time()
    cached = _pm_event_cache.get(key)
    if cached and now - cached[0] < 300:
        return cached[1]
    events: list[dict] = []
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{GAMMA}/events",
                params={
                    "tag_slug": "fifa-world-cup",
                    "limit": 100,
                    "offset": offset,
                    "closed": "false",
                    "active": "true",
                },
                timeout=15,
            )
            if not r.ok:
                break
            batch = r.json() or []
        except requests.RequestException:
            break
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    _pm_event_cache[key] = (now, events)
    return events


def _find_over25_market(home_raw: str, away_raw: str) -> Optional[dict]:
    """
    For a live api-football fixture, find the matching PM Over 2.5 market.
    Returns the Gamma market dict (with bestAsk, bestBid, clobTokenIds) or None.
    """
    events = _fetch_wc_events()
    target_pairs = [
        (_norm(home_raw), _norm(away_raw)),
        (_norm(away_raw), _norm(home_raw)),
    ]
    for ev in events:
        slug = ev.get("slug", "")
        title = ev.get("title", "")
        if " - " in title or " vs. " not in title:
            continue
        ev_home, ev_away = [s.strip() for s in title.split(" vs. ", 1)]
        eh, ea = _norm(ev_home), _norm(ev_away)
        if (eh, ea) not in target_pairs and (ea, eh) not in target_pairs:
            # also try substring tolerance for "Türkiye" vs "Turkey" etc.
            hit = False
            for nh, na in target_pairs:
                if (nh in eh or eh in nh) and (na in ea or ea in na):
                    hit = True
                    break
            if not hit:
                continue
        ou_slug = f"{slug}-total-2pt5"
        try:
            r = requests.get(
                f"{GAMMA}/markets",
                params={"slug": ou_slug, "closed": "false"},
                timeout=15,
            )
            if not r.ok:
                return None
            arr = r.json()
            if arr:
                return arr[0]
        except requests.RequestException:
            return None
    return None


def _over_token(mkt: dict) -> Optional[str]:
    """PM stores [Yes, No] in clobTokenIds; for O/U markets that's [Over, Under]."""
    raw = mkt.get("clobTokenIds") or mkt.get("clob_token_ids")
    if not raw:
        return None
    try:
        ids = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(ids, list) or len(ids) < 2:
        return None
    return str(ids[0])


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


# ── Main cycle ────────────────────────────────────────────────────────────────


def _team_in_wc(name: str) -> bool:
    n = _norm(name)
    return any(_norm(t) == n or _norm(t) in n or n in _norm(t) for t in WC_TEAMS)


def cycle() -> dict:
    """One scan: fetch live, find candidates, enter eligible ones. Returns counters."""
    summary = {"live_total": 0, "live_wc": 0, "candidates": 0, "skipped": 0, "entered": 0}
    fixtures = _fetch_live_football()
    summary["live_total"] = len(fixtures)
    if not fixtures:
        return summary

    conn = _conn()
    try:
        strategy_id = _get_or_create_strategy(conn)
        today, total = _exposure(conn, strategy_id)
        if total >= TOTAL_CAP_USD:
            log.warning(f"TOTAL CAP HIT (${total:.2f} ≥ ${TOTAL_CAP_USD}) — daemon idle")
            return summary
        if today >= DAILY_CAP_USD:
            log.warning(f"DAILY CAP HIT (${today:.2f} ≥ ${DAILY_CAP_USD}) — skipping cycle")
            return summary

        for fix in fixtures:
            teams = fix.get("teams") or {}
            home = (teams.get("home") or {}).get("name", "")
            away = (teams.get("away") or {}).get("name", "")
            if not home or not away:
                continue
            if not (_team_in_wc(home) and _team_in_wc(away)):
                continue
            summary["live_wc"] += 1

            goals = fix.get("goals") or {}
            status = (fix.get("fixture") or {}).get("status") or {}
            minute = status.get("elapsed")
            hg, ag = goals.get("home"), goals.get("away")
            if minute is None or hg is None or ag is None:
                continue

            total_goals = hg + ag
            # Diagnostic: show every WC live fixture we see so we can tell
            # whether the minute/score filter is the bottleneck.
            log.info(
                f"  [wc-live] {home} v {away} @ {minute}' {hg}-{ag} "
                f"(min∈[{MINUTE_MIN},{MINUTE_MAX}], score≤{SCORE_MAX})"
            )
            if not (MINUTE_MIN <= minute <= MINUTE_MAX):
                log.info(f"    skip: minute {minute} outside window")
                continue
            if total_goals > SCORE_MAX:
                log.info(f"    skip: score {hg}-{ag} > {SCORE_MAX}g")
                continue

            today_str = datetime.now(timezone.utc).date().isoformat()
            if _already_entered(conn, strategy_id, home, away, today_str):
                continue

            mkt = _find_over25_market(home, away)
            if not mkt:
                log.info(f"  {home} v {away} @ {minute}' {hg}-{ag} — no PM O/U 2.5 market")
                continue

            ask = _f(mkt.get("bestAsk"))
            bid = _f(mkt.get("bestBid"))
            yes_p = ask if ask is not None else _f(mkt.get("lastTradePrice"))
            if yes_p is None or yes_p <= 0:
                continue
            summary["candidates"] += 1

            if yes_p > PRICE_MAX:
                log.info(
                    f"  {home} v {away} @ {minute}' {hg}-{ag} — ask {yes_p:.3f} "
                    f"> {PRICE_MAX} (skip)"
                )
                summary["skipped"] += 1
                continue

            # Per-order notional check on top of executor cap
            stake_usd = float(os.environ.get("PM_LIVE_STAKE_USD", "1.0"))
            # Same sizing live_executor will use when it submits, so the daily-cap
            # check below is measured against the real notional.
            size = live_executor.shares_for_stake(stake_usd, yes_p)
            notional = size * yes_p
            if today + notional > DAILY_CAP_USD:
                log.warning(
                    f"  would breach daily cap (today {today:.2f} + this {notional:.2f} "
                    f"> {DAILY_CAP_USD}) — skip"
                )
                summary["skipped"] += 1
                continue

            cond_id = mkt.get("conditionId") or mkt.get("condition_id")
            end_dt = None
            try:
                v = mkt.get("endDate") or mkt.get("gameStartTime")
                if v:
                    end_dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                pass
            title = mkt.get("question") or f"{home} vs. {away}: O/U 2.5"
            market_db_id = _upsert_market(conn, cond_id, title, end_dt)
            conn.commit()

            score_str = f"{hg}-{ag}"
            reasoning = (
                f"B1 rule: ask {yes_p:.3f} ≤ {PRICE_MAX}, score {score_str} ≤ {SCORE_MAX}g, "
                f"minute {minute} ∈ [{MINUTE_MIN},{MINUTE_MAX}]."
            )
            tid = _write_trade(
                conn, strategy_id, market_db_id, yes_p, minute, score_str, reasoning
            )
            log.info(
                f"  → trade #{tid} {home} v {away} @ {minute}' {score_str} | "
                f"over 2.5 ask={yes_p:.3f} bid={bid} stake={stake_usd}u "
                f"notional=${notional:.2f}"
            )

            if LIVE_ENABLED:
                token = _over_token(mkt)
                kickoff_date = end_dt.date() if end_dt else None
                live_executor.try_execute(
                    conn,
                    trade_id=tid,
                    token_id=token,
                    side="BUY",
                    price=yes_p,
                    ask=ask,
                    fair_prob=None,           # no model filter — B1 is price-only
                    stake_usd=stake_usd,
                    home=home, away=away,
                    kickoff_date=kickoff_date,
                    outcome_key="over_2_5",
                )
            else:
                log.info(f"    [SHADOW MODE] live_executor skipped (WC_LIVE_OVERS=0)")
            summary["entered"] += 1
            today += notional  # so subsequent matches in same cycle respect the cap
    finally:
        conn.close()
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    args = ap.parse_args()

    mode = "LIVE" if LIVE_ENABLED else "SHADOW"
    log.info(
        f"START | mode={mode} | price≤{PRICE_MAX} score≤{SCORE_MAX}g min∈[{MINUTE_MIN},{MINUTE_MAX}] "
        f"daily_cap=${DAILY_CAP_USD} total_cap=${TOTAL_CAP_USD} poll={POLL_INTERVAL}s"
    )
    if args.once:
        s = cycle()
        log.info(f"DONE | {s}")
        return
    while True:
        try:
            s = cycle()
            if s["live_wc"] > 0 or s["entered"] > 0:
                log.info(f"cycle: {s}")
        except Exception as exc:
            log.exception(f"cycle error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
