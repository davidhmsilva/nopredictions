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
import resolver as _resolver
from injury_tracker import InjuryTracker
from market_flow import MarketFlow
import live_executor
import calibrator as _calibrator_mod

_calibrator = _calibrator_mod.get()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from db import find_match_id

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA_API = "https://gamma-api.polymarket.com"
PARAMS_PATH = os.path.join(os.path.dirname(__file__), "dc_model_params.json")
ALIASES_PATH = os.path.join(os.path.dirname(__file__), "team_aliases.json")
STRATEGY_NAME = "DC Model Pre-Match"
NO_BIAS_STRATEGY_NAME = "No Bias (DC Model)"
STAKE_UNITS = 1.0
DEFAULT_EDGE_THRESHOLD_PP = 3.0
NO_BIAS_EDGE_THRESHOLD_PP = 3.0
NO_BIAS_PRICE_MIN = 0.15
NO_BIAS_PRICE_MAX = 0.45
DEFAULT_DAYS_AHEAD = 3

# ── Live-eligible pockets (2026-06-01) ───────────────────────────────────────
# DC sub-strategy P&L audit: only DRAW (+16u, +52% yield) and HOME UNDERDOG
# (+12u, +60% yield) are profitable. HOME mid is ~flat-negative and AWAY (esp.
# away underdog: −15u, 12% win over 41 bets) is the sink — the model overrates
# away underdogs. So with REAL money the DC scanner now only submits draw +
# home-underdog; everything else is still logged as PAPER so we keep measuring.
# A home pick counts as an underdog when its YES price is at/below this line
# (the clean pocket in the data was ≤0.35; default 0.45 = priced < even-money).
DC_LIVE_DRAW = os.environ.get("PM_DC_LIVE_DRAW", "1") == "1"
DC_LIVE_HOME_UNDERDOG = os.environ.get("PM_DC_LIVE_HOME_UNDERDOG", "1") == "1"
DC_HOME_UNDERDOG_MAX = float(os.environ.get("PM_DC_HOME_UNDERDOG_MAX", "0.45"))

# Isotonic calibration layer (calibrate_model.py). DEFAULT OFF: it fits the
# historical match distribution perfectly in-sample, but OUT-OF-SAMPLE on our
# actual settled bets it made Brier WORSE (0.205 → 0.264) — the bet sample is a
# selected subset (only where the model most disagrees with PM) that the global
# historical fit doesn't transfer to. Kept, gated, for a future re-fit on
# accumulated live results / walk-forward. Do not enable for live money until it
# beats raw on a held-out bet sample.
USE_CALIBRATION = os.environ.get("PM_USE_CALIBRATION", "0") == "1"


def _dc_live_eligible(outcome_key: str, yes_p: float) -> bool:
    """True if this DC pick may be submitted with real money. Everything else is
    paper-only (still logged, just not sent on-chain)."""
    if outcome_key == "draw":
        return DC_LIVE_DRAW
    if outcome_key == "home":
        return DC_LIVE_HOME_UNDERDOG and yes_p <= DC_HOME_UNDERDOG_MAX
    return False  # away (and any other side) — paper only

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


def _get_or_create_no_bias_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = %s LIMIT 1", (NO_BIAS_STRATEGY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO research_hypotheses
            (title, description, rationale, source, status, created_by)
        VALUES (
            'No Bias — Fading Overpriced Favorites on Polymarket',
            'PM "Will X win?" markets overprice favorites due to retail bias. '
            'Buying "No" (draw + loss) at 15-45¢ exploits this systematically.',
            'Favorite-longshot bias: retail bettors buy Yes on popular teams, '
            'inflating win probability. The No side (draw+loss combined) is '
            'underpriced. DC model provides fair value to quantify the edge.',
            'agent', 'live', 'agent'
        ) RETURNING id
    """)
    hyp_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("""
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        RETURNING id
    """, (hyp_id, NO_BIAS_STRATEGY_NAME, json.dumps({
        "model": "Dixon-Coles xG",
        "side": "No (draw + loss)",
        "price_range": f"{NO_BIAS_PRICE_MIN}-{NO_BIAS_PRICE_MAX}",
        "edge_threshold_pp": NO_BIAS_EDGE_THRESHOLD_PP,
        "stake": "1u flat",
        "phase": "paper-only",
        "inspiration": "vpenguin-style contrarian No buyer",
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy '{NO_BIAS_STRATEGY_NAME}' (id={strat_id})")
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


def _already_traded(conn, strategy_id: int, market_db_id: int, outcome: str,
                    match_id: int | None = None,
                    home: str | None = None, away: str | None = None) -> bool:
    cur = conn.cursor()
    if match_id:
        cur.execute("""
            SELECT id FROM paper_trades
            WHERE strategy_id = %s AND match_id = %s AND result IS NULL
            LIMIT 1
        """, (strategy_id, match_id))
        if cur.fetchone():
            return True
    # Check by team names in reasoning — catches cases where the same match
    # gets a different "best edge" outcome on a subsequent scanner run
    if home and away:
        pattern = f"%{home} vs {away}%"
        cur.execute("""
            SELECT id FROM paper_trades
            WHERE strategy_id = %s AND reasoning LIKE %s AND result IS NULL
            LIMIT 1
        """, (strategy_id, pattern))
        if cur.fetchone():
            return True
    cur.execute("""
        SELECT id FROM paper_trades
        WHERE strategy_id = %s AND market_id = %s AND outcome = %s
          AND result IS NULL
        LIMIT 1
    """, (strategy_id, market_db_id, outcome))
    return cur.fetchone() is not None


def _write_trade(conn, strategy_id: int, market_db_id: int, outcome: str,
                 entry_price: float, dc_prob: float, edge_pp: float,
                 reasoning: str, match_id: Optional[int] = None,
                 home: str | None = None, away: str | None = None) -> Optional[int]:
    if _already_traded(conn, strategy_id, market_db_id, outcome,
                       match_id=match_id, home=home, away=away):
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


def _pm_token_id(mkt: dict, side_label: str) -> Optional[str]:
    """
    Extract Yes/No token_id from a PM market.
    side_label is "yes" (=index 0) or "no" (=index 1).
    PM Gamma serialises clobTokenIds as a JSON string by convention.
    """
    raw = mkt.get("clobTokenIds") or mkt.get("clob_token_ids")
    if not raw:
        return None
    try:
        ids = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(ids, list) or len(ids) < 2:
        return None
    return str(ids[0] if side_label == "yes" else ids[1])


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


# Tokens that signal a non-football sport when present in the team name.
# Real Zaragoza (futebol) shares the name with Basket Zaragoza (Liga ACB) — the
# substring fallback in _find_team would otherwise match the basketball event.
NON_FOOTBALL_NAME_TOKENS = {
    "basket", "basketball", "tennis", "atp", "wta", "nba", "ncaa",
    "baseball", "mlb", "nfl", "nhl", "mma", "ufc", "boxing",
    "cricket", "rugby", "golf", "f1", "formula", "hockey",
    "volleyball", "handball", "esports", "lol", "dota", "csgo",
}

# Polymarket tag labels that mark an event as non-football.
NON_FOOTBALL_TAG_LABELS = {
    "Basketball", "Tennis", "Baseball", "NFL", "NHL", "NBA", "NCAA",
    "MMA", "UFC", "Boxing", "Golf", "Cricket", "Rugby", "F1",
    "Formula 1", "Hockey", "Volleyball", "Handball", "Esports",
    "League of Legends", "Counter Strike", "Dota", "Valorant",
    "Crypto", "Finance", "Politics", "Weather", "Equities", "Stocks",
}


def _is_football_event(event: dict) -> bool:
    """Whitelist by `Soccer` tag, with a non-football-tag blacklist as backstop.

    Polymarket events expose `tags` as a list of {label, slug, ...}.  All
    football markets we want carry the 'Soccer' tag; basketball, tennis, etc.
    carry their own sport tag.  Default to False when tags are missing — that's
    the conservative side (better to miss a match than to misprice a basket
    game with football lambdas).
    """
    tags = event.get("tags") or []
    labels = {(t.get("label") or "").strip() for t in tags}
    if labels & NON_FOOTBALL_TAG_LABELS:
        return False
    return "Soccer" in labels


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

# Goals markets (totals + BTTS) the model overprices with no sharp line to validate.
# Per the 2026-05-29 P&L audit these bled −24u while 1X2/halftime/handicap are
# CLV-positive. Disabled until goal scoring is recalibrated (xg_multiplier=1.5x
# looks too hot). Override with DC_ENABLE_GOALS_MARKETS=1.
DISABLED_OUTCOMES: set[str] = (
    set() if os.environ.get("DC_ENABLE_GOALS_MARKETS") == "1"
    else {"over_2.5", "under_2.5", "over_1.5", "under_1.5", "btts"}
)


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


# ── Injury adjustments ────────────────────────────────────────────────────────

def _get_team_ids_api(home: str, away: str) -> tuple[int, int] | None:
    """Look up api-football team IDs by team names."""
    api_key = os.getenv('FOOTBALL_API_KEY', '')
    if not api_key:
        return None
    try:
        resp = requests.get(
            'https://v3.football.api-sports.io/fixtures',
            params={'live': 'all'},
            headers={'x-apisports-key': api_key},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        for fixture in resp.json().get('response', []):
            h_name = fixture.get('teams', {}).get('home', {}).get('name', '').lower()
            a_name = fixture.get('teams', {}).get('away', {}).get('name', '').lower()
            if h_name == home.lower() and a_name == away.lower():
                h_id = fixture.get('teams', {}).get('home', {}).get('id')
                a_id = fixture.get('teams', {}).get('away', {}).get('id')
                if h_id and a_id:
                    return int(h_id), int(a_id)
        return None
    except Exception:
        return None


def _apply_injury_adjustments(pred: dict, home: str, away: str) -> dict:
    """Apply injury adjustments to DC model prediction and recalculate probabilities."""
    try:
        from scipy.stats import poisson as poisson_dist

        injury_tracker = InjuryTracker()
        team_ids = _get_team_ids_api(home, away)
        if not team_ids:
            return pred

        home_tid, away_tid = team_ids
        inj_adj = injury_tracker.get_fixture_adjustments(0, home_tid, away_tid)

        # If no significant injuries, return unchanged
        if inj_adj['home'] >= 0.99 and inj_adj['away'] >= 0.99:
            return pred

        # Apply adjustments to lambdas
        lam_h = pred.get('lambda_home', 1.0) * inj_adj['home']
        lam_a = pred.get('lambda_away', 1.0) * inj_adj['away']

        # Recalculate 1x2 probabilities
        MAX_GOALS = 8
        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0
        for i in range(MAX_GOALS + 1):
            p_i = poisson_dist.pmf(i, lam_h)
            for j in range(MAX_GOALS + 1):
                p_j = poisson_dist.pmf(j, lam_a)
                p_ij = p_i * p_j
                if i > j:
                    p_home += p_ij
                elif i == j:
                    p_draw += p_ij
                else:
                    p_away += p_ij

        total = p_home + p_draw + p_away
        if total > 0:
            p_home /= total
            p_draw /= total
            p_away /= total

        # Update prediction with adjusted values
        pred_adj = pred.copy()
        pred_adj['lambda_home'] = lam_h
        pred_adj['lambda_away'] = lam_a
        pred_adj['home_win'] = p_home
        pred_adj['draw'] = p_draw
        pred_adj['away_win'] = p_away
        pred_adj['injury_adjustment'] = inj_adj

        return pred_adj
    except Exception as e:
        log.debug(f"Injury adjustment error (non-fatal): {e}")
        return pred


# ── Fetch PM events ───────────────────────────────────────────────────────────

# Substrings that mark a women's competition in any PM slug/title we've seen.
# PM titles often omit "Women" (just shows "Sweden vs. Italy"), so the slug is
# the only reliable signal. Our DC model is trained on men's football → must
# skip these entirely to avoid systematic mispricing.
_WOMEN_SLUG_SUBSTRINGS = (
    "wwc", "weuro", "wwcq", "wcl-w",   # PM-style women's competition prefixes
    "women", "feminin", "ladies",       # plain-language markers
    "-w-",                              # team-vs-team women's tag (e.g. "eng-w-vs-fra-w")
)


def _is_women_event(event: dict) -> bool:
    """True if any slug/title field on the event or its markets references women."""
    haystack_fields = [
        event.get("slug", ""),
        event.get("title", ""),
        event.get("description", ""),
        event.get("seriesSlug", ""),
        event.get("category", ""),
    ]
    for mkt in event.get("markets", []) or []:
        haystack_fields.append(mkt.get("slug", ""))
        haystack_fields.append(mkt.get("groupItemTitle", ""))
        haystack_fields.append(mkt.get("question", ""))
    blob = " ".join(str(x) for x in haystack_fields).lower()
    return any(tok in blob for tok in _WOMEN_SLUG_SUBSTRINGS)


def _fetch_pm_events(days_ahead: int) -> list[dict]:
    """Paginate Gamma /events using simple YYYY-MM-DD dates.

    The Gamma API silently excludes restricted/negRisk events (all individual
    match markets) when end_date_min/max use ISO timestamps.  Simple date
    strings work correctly.  tag_slug is unreliable and omitted.

    Filters out women's competitions — DC model is trained on men's football
    only and systematically mis-prices women's matches.
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

    n_raw = len(events)
    events = [e for e in events if not _is_women_event(e)]
    n_dropped = n_raw - len(events)
    if n_dropped:
        log.info(f"Filtered out {n_dropped} women's events (DC model is men-only)")
    log.info(f"Fetched {len(events)} PM events ({date_min} → {date_max})")
    return events


# ── Main ──────────────────────────────────────────────────────────────────────

def run(days_ahead: int = DEFAULT_DAYS_AHEAD,
        threshold_pp: float = DEFAULT_EDGE_THRESHOLD_PP,
        dry_run: bool = False) -> dict:

    log.info(f"Loading DC model from {PARAMS_PATH}...")
    model = DixonColesModel.load(PARAMS_PATH)

    # Initialize market flow tracker
    market_flow = MarketFlow()

    # Build normalised team index for fuzzy lookup
    norm_idx: dict[str, int] = {_norm(t): i for i, t in enumerate(model.teams)}

    log.info(f"Fetching PM events (next {days_ahead} days)...")
    events = _fetch_pm_events(days_ahead)
    log.info(f"  {len(events)} events retrieved")

    conn = None if dry_run else _conn()

    try:
        strategy_id = None if dry_run else _get_or_create_strategy(conn)
        no_bias_strategy_id = None if dry_run else _get_or_create_no_bias_strategy(conn)

        n_matched = 0
        n_edges = 0
        n_no_bias_edges = 0
        trades_logged = []
        no_bias_trades_logged = []

        n_skipped_non_football = 0
        for event in events:
            title = event.get("title", "")
            end_date = event.get("endDate", "")

            # Skip non-football sports (Basket, Tennis, NBA, etc.)
            if not _is_football_event(event):
                n_skipped_non_football += 1
                continue

            # Skip non-match events (outrights, props, etc.)
            if re.search(r"halftime|exact score|player props|corners|more markets|winner|champion", title, re.I):
                continue

            # Extract teams
            clean = re.sub(r"\s+(FC|CF|SC|AFC|RC|CD|RCD|FK|SK)$", "", title, flags=re.I)
            m = re.match(r"(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s*[-:]|$)", clean, re.I)
            if not m:
                continue
            home_raw, away_raw = m.group(1).strip(), m.group(2).strip()

            # Belt-and-suspenders: refuse names containing non-football tokens
            # even if the tag check let it through.
            raw_lower = f"{home_raw} {away_raw}".lower()
            if any(tok in raw_lower.split() for tok in NON_FOOTBALL_NAME_TOKENS):
                n_skipped_non_football += 1
                continue

            # Fuzzy match both teams against model
            h_idx = _find_team(home_raw, norm_idx)
            a_idx = _find_team(away_raw, norm_idx)
            if h_idx is None or a_idx is None:
                continue

            home = model.teams[h_idx]
            away = model.teams[a_idx]

            try:
                pred = model.predict(home, away)
                # Apply injury adjustments to prediction
                pred = _apply_injury_adjustments(pred, home, away)
                # Recalibrate the 1X2 probabilities (shrinks model over-confidence).
                if USE_CALIBRATION:
                    pred = _calibrator.calibrate_1x2(pred, home, away)
            except Exception:
                continue

            n_matched += 1

            # Helper to resolve kickoff date + match_id (shared by both strategies)
            kickoff_date = None
            if end_date:
                try:
                    kickoff_date = datetime.fromisoformat(
                        end_date.replace("Z", "+00:00")).date()
                except (ValueError, TypeError):
                    pass
            db_match_id = find_match_id(conn, home, away, kickoff_date) if not dry_run else None

            # ── Strategy 1: DC Model Pre-Match (Yes side) ────────────────────
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
                if outcome_key in DISABLED_OUTCOMES:
                    continue

                dc_key = DC_KEY_MAP.get(outcome_key)
                dc_prob = pred.get(dc_key) if dc_key else None
                if dc_prob is None:
                    continue

                edge_pp = round((dc_prob - yes_p) * 100, 1)
                if edge_pp < threshold_pp:
                    continue

                n_edges += 1
                inj_note = ""
                if pred.get('injury_adjustment'):
                    inj_adj = pred.get('injury_adjustment')
                    inj_note = f" [Injuries: H={inj_adj['home']:.2%} A={inj_adj['away']:.2%}]"

                # Check market flow for smart money agreement
                flow_note = ""
                try:
                    if mkt.get('id'):
                        market_id = mkt.get('id')
                        # For pre-match, "yes" edge means betting on the yes side
                        flow_direction = 'yes' if edge_pp > 0 else 'no'
                        flow_signal = market_flow.detect_smart_money_signal(market_id, flow_direction)
                        if flow_signal and flow_signal.whale_volume >= 10000:
                            if flow_signal.confidence >= 0.7:
                                flow_note = f" | Whale {flow_signal.whale_side}"
                except Exception:
                    pass

                reasoning = (
                    f"DC Model: {home} vs {away} — {question}. "
                    f"PM price: {yes_p*100:.1f}% ({1/yes_p:.2f}). "
                    f"DC fair value: {dc_prob*100:.1f}% ({1/dc_prob:.2f}). "
                    f"Edge: +{edge_pp:.1f}pp. "
                    f"λ home={pred['lambda_home']:.2f} λ away={pred['lambda_away']:.2f}.{inj_note}"
                )

                log.info(
                    f"  EDGE +{edge_pp:.1f}pp | {home} vs {away} | {outcome_key} | "
                    f"PM={yes_p*100:.1f}% DC={dc_prob*100:.1f}%{inj_note}{flow_note}"
                )

                match_candidates.append({
                    "mkt": mkt, "outcome_key": outcome_key, "yes_p": yes_p,
                    "dc_prob": dc_prob, "edge_pp": edge_pp, "reasoning": reasoning,
                })

            # Log the single best edge for this match (paper measurement). For
            # LIVE we separately pick the best *eligible* pick (draw / home-
            # underdog) — the global best may be an away pick that we don't fund,
            # which used to leave a perfectly good eligible draw unbet.
            if match_candidates and not dry_run:
                best = max(match_candidates, key=lambda c: c["edge_pp"])
                best_live = max(
                    (c for c in match_candidates
                     if _dc_live_eligible(c["outcome_key"], c["yes_p"])),
                    key=lambda c: c["edge_pp"], default=None,
                )

                def _log_and_maybe_execute(cand: dict, *, execute: bool) -> Optional[int]:
                    ext_id = str(cand["mkt"].get("id") or cand["mkt"].get("conditionId") or "")
                    mkt_db = _upsert_pm_market(conn, ext_id, cand["mkt"].get("question", ""), end_date)
                    if not mkt_db:
                        return None
                    tid = _write_trade(
                        conn, strategy_id, mkt_db, cand["outcome_key"],
                        cand["yes_p"], cand["dc_prob"], cand["edge_pp"], cand["reasoning"],
                        match_id=db_match_id, home=home, away=away,
                    )
                    if not tid:
                        return None
                    trades_logged.append(tid)
                    if execute:
                        live_executor.try_execute(
                            conn, trade_id=tid,
                            token_id=_pm_token_id(cand["mkt"], "yes"),
                            side="BUY", price=cand["yes_p"],
                            ask=cand["mkt"].get("bestAsk"),
                            fair_prob=cand["dc_prob"],
                            home=home, away=away, kickoff_date=kickoff_date,
                            outcome_key=cand["outcome_key"],
                        )
                    return tid

                if db_match_id:
                    log.info(f"    → Linked to match_id={db_match_id}")

                # Filters override the raw-best rule:
                # If a live-eligible pick exists, it is the primary paper trade
                # (logged first so _already_traded doesn't block it). The raw
                # best is only logged when no eligible candidate has edge.
                if best_live is not None:
                    live_tid = _log_and_maybe_execute(best_live, execute=True)
                    if live_tid:
                        log.info(
                            f"    → Trade #{live_tid} logged + live-submitted "
                            f"({best_live['outcome_key']} @ {best_live['yes_p']:.2f}, "
                            f"+{best_live['edge_pp']:.1f}pp)"
                        )
                        if best is not best_live:
                            log.info(
                                f"    → Raw best ({best['outcome_key']} @ {best['yes_p']:.2f}, "
                                f"+{best['edge_pp']:.1f}pp) skipped — filters take priority"
                            )
                    else:
                        log.info(f"    → Skipped (already traded this match)")
                else:
                    # No live-eligible edge — log raw best as paper-only for research.
                    best_tid = _log_and_maybe_execute(best, execute=False)
                    if best_tid:
                        log.info(
                            f"    → Trade #{best_tid} logged (paper-only, "
                            f"no live-eligible pocket) "
                            f"[{best['outcome_key']} @ {best['yes_p']:.2f}]"
                        )
                    log.info(
                        f"    → no live-eligible pocket this match "
                        f"(best {best['outcome_key']} @ {best['yes_p']:.2f}) — paper only"
                    )

            # ── Strategy 2: No Bias — fade overpriced favorites ──────────────
            no_bias_candidates: list[dict] = []

            for mkt in event.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue

                question = mkt.get("question", "")
                raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                if not raw:
                    continue
                prices = json.loads(raw) if isinstance(raw, str) else raw
                yes_p = float(prices[0])
                no_p = round(1.0 - yes_p, 6)

                # No Bias only targets win markets (home/away)
                outcome_key = _classify_market(question, home, away)
                if outcome_key not in ("home", "away"):
                    continue

                # Price filter: No side must be in the sweet spot
                if no_p < NO_BIAS_PRICE_MIN or no_p > NO_BIAS_PRICE_MAX:
                    continue

                dc_key = DC_KEY_MAP.get(outcome_key)
                dc_win_prob = pred.get(dc_key) if dc_key else None
                if dc_win_prob is None:
                    continue

                dc_no_prob = 1.0 - dc_win_prob
                no_edge_pp = round((dc_no_prob - no_p) * 100, 1)

                if no_edge_pp < NO_BIAS_EDGE_THRESHOLD_PP:
                    continue

                n_no_bias_edges += 1
                team_faded = home if outcome_key == "home" else away
                no_outcome = f"NOT {team_faded} win"
                reasoning = (
                    f"No Bias: {home} vs {away} — fade {team_faded}. "
                    f"PM Yes price: {yes_p*100:.1f}% → No price: {no_p*100:.1f}% ({1/no_p:.2f}). "
                    f"DC fair No: {dc_no_prob*100:.1f}% ({1/dc_no_prob:.2f}). "
                    f"Edge: +{no_edge_pp:.1f}pp. "
                    f"λ home={pred['lambda_home']:.2f} λ away={pred['lambda_away']:.2f}."
                )

                log.info(
                    f"  NO-BIAS +{no_edge_pp:.1f}pp | {home} vs {away} | "
                    f"fade {team_faded} | No={no_p*100:.1f}% DC_No={dc_no_prob*100:.1f}%"
                )

                no_bias_candidates.append({
                    "mkt": mkt, "outcome_key": no_outcome, "no_p": no_p,
                    "dc_no_prob": dc_no_prob, "edge_pp": no_edge_pp, "reasoning": reasoning,
                })

            if no_bias_candidates and not dry_run:
                best = max(no_bias_candidates, key=lambda c: c["edge_pp"])
                ext_id = str(best["mkt"].get("id") or best["mkt"].get("conditionId") or "")
                question = best["mkt"].get("question", "")
                market_db_id = _upsert_pm_market(conn, ext_id, question, end_date)
                if market_db_id:
                    trade_id = _write_trade(
                        conn, no_bias_strategy_id, market_db_id, best["outcome_key"],
                        best["no_p"], best["dc_no_prob"], best["edge_pp"], best["reasoning"],
                        match_id=db_match_id, home=home, away=away,
                    )
                    if trade_id:
                        log.info(f"    → No Bias trade #{trade_id} logged")
                        no_bias_trades_logged.append(trade_id)
                        # Executable ask for the NO token = 1 - bestBid(yes).
                        _yes_bid = best["mkt"].get("bestBid")
                        _no_ask = (1.0 - float(_yes_bid)) if _yes_bid is not None else None
                        live_executor.try_execute(
                            conn, trade_id=trade_id,
                            token_id=_pm_token_id(best["mkt"], "no"),
                            side="BUY", price=best["no_p"],
                            ask=_no_ask,
                            fair_prob=best["dc_no_prob"],
                            home=home, away=away, kickoff_date=kickoff_date,
                            outcome_key=best["outcome_key"],
                        )
                    else:
                        log.info("    → No Bias: already logged today, skipped")

        if not dry_run:
            _log_run(conn, len(events), n_matched, n_edges + n_no_bias_edges, dry_run)

        # ── Resolver: settle any open trades whose markets have now closed ──
        resolved = []
        if not dry_run:
            try:
                resolved = _resolver.run()
                if resolved:
                    log.info(f"[resolver] Settled {len(resolved)} trade(s) after scan")
            except Exception as exc:
                log.warning(f"[resolver] Failed to run after scan: {exc}")

        summary = {
            "events_scanned": len(events),
            "non_football_skipped": n_skipped_non_football,
            "matches_in_model": n_matched,
            "dc_edges_found": n_edges,
            "dc_trades_logged": len(trades_logged),
            "no_bias_edges_found": n_no_bias_edges,
            "no_bias_trades_logged": len(no_bias_trades_logged),
            "resolved_trades": len(resolved),
            "dry_run": dry_run,
        }
        log.info(
            f"Done — {n_skipped_non_football} non-football skipped | "
            f"{n_matched} matches in model | "
            f"DC: {n_edges} edges, {len(trades_logged)} trades | "
            f"No Bias: {n_no_bias_edges} edges, {len(no_bias_trades_logged)} trades"
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
