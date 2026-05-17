"""
DC Model Daily Scanner — runs every morning, scans Polymarket football markets
for the next 3 days, compares prices against our Dixon-Coles model, and writes
paper trades to the DB for any edge ≥ threshold.

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python dc_scanner.py              # live run (writes to DB)
  python dc_scanner.py --dry-run    # no DB writes, just print
  python dc_scanner.py --days 5     # extend window to 5 days
  python dc_scanner.py --threshold 4.0  # stricter edge threshold
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from dixon_coles import DixonColesModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from db import find_match_id

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA_API = "https://gamma-api.polymarket.com"
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")
ALIASES_PATH = os.path.join(os.path.dirname(__file__), "team_aliases.json")
STRATEGY_NAME = "DC Model Pre-Match"
STAKE_UNITS = 1.0
DEFAULT_EDGE_THRESHOLD_PP = 3.0
DEFAULT_DAYS_AHEAD = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [dc_scanner] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dc_scanner")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _serial(obj):
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if hasattr(obj, "__float__"):
        return float(obj)
    raise TypeError(f"Not serialisable: {type(obj)}")


def _get_or_create_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = %s LIMIT 1", (STRATEGY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO research_hypotheses
            (title, description, rationale, source, status, created_by)
        VALUES (
            'DC Model Pre-Match Edge Scanner',
            'Polymarket pre-match prices diverge from Dixon-Coles xG model fair value.',
            'DC model trained on 104k matches + xG signal. Edges = markets where PM '
            'price is significantly below DC fair probability.',
            'agent', 'live', 'agent'
        ) RETURNING id
    """)
    hyp_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("""
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        RETURNING id
    """, (hyp_id, STRATEGY_NAME, json.dumps({
        "model": "Dixon-Coles xG",
        "benchmark": "DC model fair probability",
        "edge_threshold_pp": DEFAULT_EDGE_THRESHOLD_PP,
        "stake": "1u flat",
        "phase": "paper-only",
        "trained_on": "104k matches, 16k with xG, Big 5 + European leagues",
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy '{STRATEGY_NAME}' (id={strat_id})")
    return strat_id


def _upsert_pm_market(conn, ext_id: str, title: str, resolution_time: Optional[str]) -> Optional[int]:
    t = title.lower()
    mtype = ("1x2" if any(x in t for x in ["win", "draw"]) else
             "over_under" if ("over" in t or "under" in t) else "btts" if "both teams" in t else "other")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pm_markets (platform, external_id, title, market_type,
                                resolution_time, status, ingested_at)
        VALUES ('polymarket', %s, %s, %s, %s, 'active', NOW())
        ON CONFLICT (platform, external_id) DO UPDATE SET
            title = EXCLUDED.title,
            resolution_time = EXCLUDED.resolution_time,
            ingested_at = NOW()
        RETURNING id
    """, (ext_id, title, mtype, resolution_time))
    row = cur.fetchone()
    return row[0] if row else None


def _already_traded(conn, strategy_id: int, market_db_id: int, outcome: str) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM paper_trades
        WHERE strategy_id = %s AND market_id = %s AND outcome = %s
          AND placed_at >= NOW() - INTERVAL '12 hours'
        LIMIT 1
    """, (strategy_id, market_db_id, outcome))
    return cur.fetchone() is not None


def _write_trade(conn, strategy_id: int, market_db_id: int, outcome: str,
                 entry_price: float, dc_prob: float, edge_pp: float,
                 reasoning: str, match_id: Optional[int] = None) -> Optional[int]:
    if _already_traded(conn, strategy_id, market_db_id, outcome):
        return None
    entry_odds = round(1 / entry_price, 4) if entry_price > 0 else None
    confidence = min(round(edge_pp / 15.0, 3), 1.0)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO paper_trades (
            strategy_id, market_id, match_id, outcome,
            entry_price, entry_odds,
            model_probability, sharp_consensus_price, sharp_consensus_sources,
            expected_edge, confidence, stake_units, reasoning, placed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
        RETURNING id
    """, (
        strategy_id, market_db_id, match_id, outcome,
        entry_price, entry_odds,
        dc_prob, dc_prob,
        json.dumps({"source": "DC Model xG", "dc_prob": round(dc_prob, 4)}),
        round(edge_pp / 100, 6),
        confidence,
        STAKE_UNITS,
        reasoning,
    ))
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


def _log_run(conn, n_events: int, n_matched: int, n_edges: int, dry_run: bool):
    summary = (
        f"{'[DRY RUN] ' if dry_run else ''}"
        f"Scanned {n_events} PM events, matched {n_matched}, found {n_edges} edges."
    )
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_runs
            (run_type, started_at, finished_at, input_context, output_summary, status)
        VALUES ('dc_scanner:daily', NOW(), NOW(), %s::jsonb, %s, 'completed')
    """, (
        json.dumps({"events": n_events, "matched": n_matched, "edges": n_edges, "dry_run": dry_run}),
        summary,
    ))
    conn.commit()


# ── Team name normalisation + fuzzy matching ──────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ",
        re.sub(r"[^a-z0-9 ]", "",
        re.sub(r"\b(fc|cf|sc|ac|ss|afc|bsc|rcd|ssc|cd|rc|sl|as|ca|aa|fk|sk|rb|vfb|vfl|sv|bv|1\.)\b", "",
        s.lower()))).strip()


# Load team aliases after _norm is defined
with open(ALIASES_PATH) as _f:
    _RAW_ALIASES: dict = json.load(_f)
TEAM_ALIASES: dict[str, str] = {_norm(k): v for k, v in _RAW_ALIASES.items()}


def _find_team(name: str, team_idx: dict[str, int]) -> Optional[int]:
    n = _norm(name)

    # 1. Alias table — explicit PM name → canonical model name
    canonical = TEAM_ALIASES.get(n)
    if canonical is not None:
        if canonical == "__NOT_IN_MODEL__":
            return None
        cn = _norm(canonical)
        if cn in team_idx:
            return team_idx[cn]

    # 2. Exact normalised match
    if n in team_idx:
        return team_idx[n]

    # 3. Prefix match (first 7 chars, min length 5 to avoid "inter" → "inter miami")
    pre = n[:7]
    if len(pre) >= 5:
        for key, idx in team_idx.items():
            if len(key) >= 5 and (key.startswith(pre) or pre.startswith(key[:7])):
                return idx

    # 4. Substring — only if the match is unambiguous (name has ≥7 chars)
    if len(n) >= 7:
        for key, idx in team_idx.items():
            if len(key) >= 7 and (key in n or n in key):
                return idx

    return None


# ── Market classification ─────────────────────────────────────────────────────

DC_KEY_MAP = {
    "home":      "home_win",
    "draw":      "draw",
    "away":      "away_win",
    "over_2.5":  "over_2_5",
    "under_2.5": "under_2_5",
    "over_1.5":  "over_1_5",
    "under_1.5": "under_1_5",
    "btts":      "btts",
}


def _classify_market(question: str, home: str, away: str) -> Optional[str]:
    q = question.lower()
    # Structural patterns first
    if "draw" in q or "end in a draw" in q:
        return "draw"
    if "over 2.5" in q:  return "over_2.5"
    if "under 2.5" in q: return "under_2.5"
    if "over 1.5" in q:  return "over_1.5"
    if "under 1.5" in q: return "under_1.5"
    if "both teams" in q and "score" in q: return "btts"
    # Team name match for win markets (min 5 chars to avoid "real", "club", "city" cross-match)
    h_words = {w for w in _norm(home).split() if len(w) >= 5}
    a_words = {w for w in _norm(away).split() if len(w) >= 5}
    q_words = set(q.split())
    if h_words & q_words: return "home"
    if a_words & q_words: return "away"
    return None


# ── Fetch PM events ───────────────────────────────────────────────────────────

def _fetch_pm_events(days_ahead: int) -> list[dict]:
    """Paginate Gamma /events using simple YYYY-MM-DD dates.

    The Gamma API silently excludes restricted/negRisk events (all individual
    match markets) when end_date_min/max use ISO timestamps.  Simple date
    strings work correctly.  tag_slug is unreliable and omitted.
    """
    now = datetime.now(timezone.utc)
    date_min = now.strftime("%Y-%m-%d")
    date_max = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    events: list[dict] = []
    page_size = 100
    for offset in range(0, 2000, page_size):
        resp = requests.get(f"{GAMMA_API}/events", params={
            "closed": "false",
            "active": "true",
            "limit": page_size,
            "offset": offset,
            "end_date_min": date_min,
            "end_date_max": date_max,
        }, timeout=15)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list) or not page:
            break
        events.extend(page)

    log.info(f"Fetched {len(events)} PM events ({date_min} → {date_max})")
    return events


# ── Main ──────────────────────────────────────────────────────────────────────

def run(days_ahead: int = DEFAULT_DAYS_AHEAD,
        threshold_pp: float = DEFAULT_EDGE_THRESHOLD_PP,
        dry_run: bool = False) -> dict:

    log.info(f"Loading DC model from {PARAMS_PATH}...")
    model = DixonColesModel.load(PARAMS_PATH)

    # Build normalised team index for fuzzy lookup
    norm_idx: dict[str, int] = {_norm(t): i for i, t in enumerate(model.teams)}

    log.info(f"Fetching PM events (next {days_ahead} days)...")
    events = _fetch_pm_events(days_ahead)
    log.info(f"  {len(events)} events retrieved")

    conn = None if dry_run else _conn()

    try:
        strategy_id = None if dry_run else _get_or_create_strategy(conn)

        n_matched = 0
        n_edges = 0
        trades_logged = []

        for event in events:
            title = event.get("title", "")
            end_date = event.get("endDate", "")

            # Skip non-match events (outrights, props, etc.)
            if re.search(r"halftime|exact score|player props|corners|more markets|winner|champion", title, re.I):
                continue

            # Extract teams
            clean = re.sub(r"\s+(FC|CF|SC|AFC|RC|CD|RCD|FK|SK)$", "", title, flags=re.I)
            m = re.match(r"(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[-:]|$)", clean, re.I)
            if not m:
                continue
            home_raw, away_raw = m.group(1).strip(), m.group(2).strip()

            # Fuzzy match both teams against model
            h_idx = _find_team(home_raw, norm_idx)
            a_idx = _find_team(away_raw, norm_idx)
            if h_idx is None or a_idx is None:
                continue

            home = model.teams[h_idx]
            away = model.teams[a_idx]

            try:
                pred = model.predict(home, away)
            except Exception:
                continue

            n_matched += 1

            # Collect all edges for this match, then write only the best one
            match_candidates: list[dict] = []

            for mkt in event.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue

                question = mkt.get("question", "")
                raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                if not raw:
                    continue
                prices = json.loads(raw) if isinstance(raw, str) else raw
                yes_p = float(prices[0])
                if yes_p <= 0.03 or yes_p >= 0.97:
                    continue

                outcome_key = _classify_market(question, home, away)
                if not outcome_key:
                    continue

                dc_key = DC_KEY_MAP.get(outcome_key)
                dc_prob = pred.get(dc_key) if dc_key else None
                if dc_prob is None:
                    continue

                edge_pp = round((dc_prob - yes_p) * 100, 1)
                if edge_pp < threshold_pp:
                    continue

                n_edges += 1
                reasoning = (
                    f"DC Model: {home} vs {away} — {question}. "
                    f"PM price: {yes_p*100:.1f}% ({1/yes_p:.2f}). "
                    f"DC fair value: {dc_prob*100:.1f}% ({1/dc_prob:.2f}). "
                    f"Edge: +{edge_pp:.1f}pp. "
                    f"λ home={pred['lambda_home']:.2f} λ away={pred['lambda_away']:.2f}."
                )

                log.info(
                    f"  EDGE +{edge_pp:.1f}pp | {home} vs {away} | {outcome_key} | "
                    f"PM={yes_p*100:.1f}% DC={dc_prob*100:.1f}%"
                )

                match_candidates.append({
                    "mkt": mkt, "outcome_key": outcome_key, "yes_p": yes_p,
                    "dc_prob": dc_prob, "edge_pp": edge_pp, "reasoning": reasoning,
                })

            # Only log the single best edge for this match
            if match_candidates and not dry_run:
                best = max(match_candidates, key=lambda c: c["edge_pp"])
                ext_id = str(best["mkt"].get("id") or best["mkt"].get("conditionId") or "")
                question = best["mkt"].get("question", "")
                market_db_id = _upsert_pm_market(conn, ext_id, question, end_date)
                if market_db_id:
                    # Link to matches table for CLV calculation at resolution time
                    kickoff_date = None
                    if end_date:
                        try:
                            kickoff_date = datetime.fromisoformat(
                                end_date.replace("Z", "+00:00")).date()
                        except (ValueError, TypeError):
                            pass
                    db_match_id = find_match_id(conn, home, away, kickoff_date)
                    if db_match_id:
                        log.info(f"    → Linked to match_id={db_match_id}")

                    trade_id = _write_trade(
                        conn, strategy_id, market_db_id, best["outcome_key"],
                        best["yes_p"], best["dc_prob"], best["edge_pp"], best["reasoning"],
                        match_id=db_match_id,
                    )
                    if trade_id:
                        log.info(f"    → Trade #{trade_id} logged (best edge of {len(match_candidates)} candidates)")
                        trades_logged.append(trade_id)
                    else:
                        log.info("    → Already logged today, skipped")

        if not dry_run:
            _log_run(conn, len(events), n_matched, n_edges, dry_run)

        summary = {
            "events_scanned": len(events),
            "matches_in_model": n_matched,
            "edges_found": n_edges,
            "trades_logged": len(trades_logged),
            "dry_run": dry_run,
        }
        log.info(
            f"Done — {n_matched} matches in model, {n_edges} edges, "
            f"{len(trades_logged)} trades logged."
        )
        return summary

    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="DC Model daily scanner for Polymarket")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS_AHEAD, help="Days ahead to scan")
    parser.add_argument("--threshold", type=float, default=DEFAULT_EDGE_THRESHOLD_PP,
                        help="Edge threshold in percentage points (default: 3.0)")
    args = parser.parse_args()

    result = run(days_ahead=args.days, threshold_pp=args.threshold, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
