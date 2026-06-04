"""
NBA Elo Scanner — scans ALL Polymarket NBA game markets (moneyline, spreads,
totals, 1H variants), compares prices against our Elo model, and writes
paper trades for any market type with sufficient edge.

Supported market types:
- Moneyline: Elo win probability (+ playoff context adjustments)
- Spread: expected margin from Elo diff, normal CDF for cover probability
- Total (O/U): team scoring averages from DB, normal CDF
- 1H Moneyline: half-game approximation of full-game Elo
- 1H Spread / 1H O/U: half-game approximations
- Skips: player props, odd/even, score-first (need player-level data)

Usage:
  cd agent && source ../ingest/.venv/bin/activate
  python nba_scanner.py              # live run
  python nba_scanner.py --dry-run    # no DB writes
  python nba_scanner.py --threshold 5.0  # stricter edge
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

from scipy.stats import norm

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../ingest/.env"))

# Add ingest dir to path for Elo model
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../ingest"))
from stage_f_nba import NBAElo

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA_API = "https://gamma-api.polymarket.com"
ELO_PATH = os.path.join(os.path.dirname(__file__), "nba_elo_ratings.json")
STRATEGY_NAME = "NBA Elo Pre-Match"
STAKE_UNITS = 1.0
DEFAULT_EDGE_THRESHOLD_PP = 4.0
DEFAULT_DAYS_AHEAD = 3

# League-wide constants (2025-26 season)
LEAGUE_AVG_TOTAL = 230.4
LEAGUE_AVG_MARGIN_STDEV = 16.5
LEAGUE_AVG_TOTAL_STDEV = 20.0
ELO_POINTS_PER_MARGIN = 25.0  # ~25 Elo points ≈ 1 point of expected margin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [nba_scanner] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nba_scanner")


# ── Team name mapping ────────────────────────────────────────────────────────

NBA_TEAM_ALIASES = {
    # PM name variants → canonical nba_api name
    "lakers": "Los Angeles Lakers",
    "la lakers": "Los Angeles Lakers",
    "clippers": "LA Clippers",
    "la clippers": "LA Clippers",
    "celtics": "Boston Celtics",
    "knicks": "New York Knicks",
    "nets": "Brooklyn Nets",
    "76ers": "Philadelphia 76ers",
    "sixers": "Philadelphia 76ers",
    "raptors": "Toronto Raptors",
    "bulls": "Chicago Bulls",
    "cavaliers": "Cleveland Cavaliers",
    "cavs": "Cleveland Cavaliers",
    "pistons": "Detroit Pistons",
    "pacers": "Indiana Pacers",
    "bucks": "Milwaukee Bucks",
    "heat": "Miami Heat",
    "magic": "Orlando Magic",
    "hawks": "Atlanta Hawks",
    "hornets": "Charlotte Hornets",
    "wizards": "Washington Wizards",
    "warriors": "Golden State Warriors",
    "thunder": "Oklahoma City Thunder",
    "okc": "Oklahoma City Thunder",
    "spurs": "San Antonio Spurs",
    "rockets": "Houston Rockets",
    "mavericks": "Dallas Mavericks",
    "mavs": "Dallas Mavericks",
    "nuggets": "Denver Nuggets",
    "timberwolves": "Minnesota Timberwolves",
    "wolves": "Minnesota Timberwolves",
    "trail blazers": "Portland Trail Blazers",
    "blazers": "Portland Trail Blazers",
    "kings": "Sacramento Kings",
    "suns": "Phoenix Suns",
    "jazz": "Utah Jazz",
    "grizzlies": "Memphis Grizzlies",
    "pelicans": "New Orleans Pelicans",
}

# Standard NBA abbreviations (as used in Polymarket slugs / teams[].abbreviation)
NBA_TEAM_ABBR = {
    "atl": "Atlanta Hawks", "bos": "Boston Celtics", "bkn": "Brooklyn Nets",
    "cha": "Charlotte Hornets", "chi": "Chicago Bulls", "cle": "Cleveland Cavaliers",
    "dal": "Dallas Mavericks", "den": "Denver Nuggets", "det": "Detroit Pistons",
    "gsw": "Golden State Warriors", "hou": "Houston Rockets", "ind": "Indiana Pacers",
    "lac": "LA Clippers", "lal": "Los Angeles Lakers", "mem": "Memphis Grizzlies",
    "mia": "Miami Heat", "mil": "Milwaukee Bucks", "min": "Minnesota Timberwolves",
    "nop": "New Orleans Pelicans", "nyk": "New York Knicks", "okc": "Oklahoma City Thunder",
    "orl": "Orlando Magic", "phi": "Philadelphia 76ers", "phx": "Phoenix Suns",
    "por": "Portland Trail Blazers", "sac": "Sacramento Kings", "sas": "San Antonio Spurs",
    "tor": "Toronto Raptors", "uta": "Utah Jazz", "was": "Washington Wizards",
}

# Build reverse lookup: canonical → canonical (identity) + aliases + abbreviations
_TEAM_LOOKUP: dict[str, str] = {}
for alias, canonical in {**NBA_TEAM_ALIASES, **NBA_TEAM_ABBR}.items():
    _TEAM_LOOKUP[alias] = canonical
    _TEAM_LOOKUP[canonical.lower()] = canonical


def _resolve_team(name: str) -> Optional[str]:
    """Resolve a PM team name to canonical nba_api name."""
    n = name.strip().lower()
    if n in _TEAM_LOOKUP:
        return _TEAM_LOOKUP[n]
    # Try last word (e.g. "San Antonio Spurs" → "spurs")
    last = n.split()[-1] if n else ""
    if last in _TEAM_LOOKUP:
        return _TEAM_LOOKUP[last]
    # Try substring
    for alias, canonical in _TEAM_LOOKUP.items():
        if alias in n or n in alias:
            return canonical
    return None


# ── Market classification ────────────────────────────────────────────────────

def _classify_nba_market(question: str) -> Optional[dict]:
    """Parse a PM market question and return market type + parameters.

    Returns None for unmodelable markets (player props, odd/even, etc.).

    Formats observed on PM:
      Moneyline:  "Knicks vs. Cavaliers"
      Spread:     "Spread: Thunder (-5.5)"
      O/U:        "Cavaliers vs. Knicks: O/U 215.5"
      1H ML:      "Cavaliers vs. Knicks: 1H Moneyline"
      1H Spread:  "1H Spread: Knicks (-3.5)"
      1H O/U:     "Cavaliers vs. Knicks: 1H O/U 104.5"
    """
    q = question.strip()

    # Player props — skip
    if re.search(r"points|assists|rebounds|three.pointer|double.double|"
                 r"triple.double|steals?|blocks?|turnovers?|free throw|"
                 r"fouls?|minutes", q, re.I):
        return None

    # Unmodelable
    if re.search(r"odd.even|score first|first to score|race to|"
                 r"lead after|exact score|most|more|mvp", q, re.I):
        return None

    # 1H Spread: "1H Spread: Knicks (-3.5)"
    m = re.match(r"1H\s+Spread:\s+(.+?)\s*\(([+-]?\d+\.?\d*)\)", q, re.I)
    if m:
        team_name = m.group(1).strip()
        line = float(m.group(2))
        return {"type": "1h_spread", "team": team_name, "line": line}

    # Full-game Spread: "Spread: Thunder (-5.5)"
    m = re.match(r"Spread:\s+(.+?)\s*\(([+-]?\d+\.?\d*)\)", q, re.I)
    if m:
        team_name = m.group(1).strip()
        line = float(m.group(2))
        return {"type": "spread", "team": team_name, "line": line}

    # 1H O/U: "Team A vs. Team B: 1H O/U 104.5"
    m = re.search(r"1H\s+O/?U\s+(\d+\.?\d*)", q, re.I)
    if m:
        total_line = float(m.group(1))
        return {"type": "1h_total", "line": total_line}

    # Full-game O/U: "Team A vs. Team B: O/U 215.5"
    m = re.search(r"O/?U\s+(\d+\.?\d*)", q, re.I)
    if m:
        total_line = float(m.group(1))
        return {"type": "total", "line": total_line}

    # 1H Moneyline: "Team A vs. Team B: 1H Moneyline"
    if re.search(r"1H\s+Moneyline", q, re.I):
        return {"type": "1h_moneyline"}

    # Moneyline: "Team A vs. Team B" (no colon suffix)
    if re.match(r".+\s+vs\.?\s+.+$", q, re.I) and ":" not in q:
        return {"type": "moneyline"}

    return None


def _expected_margin(elo_home: float, elo_away: float) -> float:
    """Expected margin (home perspective) from Elo difference."""
    return (elo_home - elo_away) / ELO_POINTS_PER_MARGIN


def _spread_cover_prob(expected_margin: float, spread_line: float,
                       stdev: float = LEAGUE_AVG_MARGIN_STDEV,
                       is_half: bool = False) -> float:
    """Probability that spread_team covers their line.

    spread_line is from the spread team's perspective (negative = favorite).
    The team covers if: actual_margin > -spread_line (from their perspective).
    We compute P(margin > -line) = P(Z > (-line - expected) / stdev).
    """
    if is_half:
        expected_margin = expected_margin * 0.5
        stdev = stdev * 0.707  # sqrt(0.5)
    return float(norm.sf(-spread_line - expected_margin, loc=0, scale=stdev))


def _total_over_prob(expected_total: float, line: float,
                     stdev: float = LEAGUE_AVG_TOTAL_STDEV,
                     is_half: bool = False) -> float:
    """Probability that the game total goes over the line."""
    if is_half:
        expected_total = expected_total * 0.48  # 1H is ~48% of total
        stdev = stdev * 0.707
    return float(norm.sf(line, loc=expected_total, scale=stdev))


def _get_team_scoring_stats(conn, team_name: str) -> Optional[dict]:
    """Get average points scored and allowed for a team from recent games."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            AVG(CASE WHEN ht.canonical_name = %s THEN m.home_score
                     ELSE m.away_score END) AS avg_scored,
            AVG(CASE WHEN ht.canonical_name = %s THEN m.away_score
                     ELSE m.home_score END) AS avg_allowed,
            COUNT(*) AS games
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id
        WHERE l.code = 'USA-NBA'
          AND m.status = 'finished'
          AND m.home_score IS NOT NULL
          AND (ht.canonical_name = %s OR at.canonical_name = %s)
          AND m.kickoff_utc > NOW() - INTERVAL '120 days'
        LIMIT 100
    """, (team_name, team_name, team_name, team_name))

    row = cur.fetchone()
    if not row or not row["avg_scored"] or row["games"] < 10:
        return None
    return {
        "avg_scored": float(row["avg_scored"]),
        "avg_allowed": float(row["avg_allowed"]),
        "games": int(row["games"]),
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


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
            'NBA Elo Pre-Match Edge Scanner',
            'Polymarket NBA game prices diverge from Elo model fair value, '
            'especially in playoff context (G7 home, momentum, sweeps).',
            'Elo model trained on 15k+ NBA games (2014-2026). PM is retail-heavy '
            'for NBA — contextual factors create systematic mispricings.',
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
        "model": "NBA Elo + Playoff Context",
        "benchmark": "Elo fair probability",
        "edge_threshold_pp": DEFAULT_EDGE_THRESHOLD_PP,
        "stake": "1u flat",
        "phase": "paper-only",
        "trained_on": "15k+ NBA games (2014-2026)",
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    log.info(f"Created strategy '{STRATEGY_NAME}' (id={strat_id})")
    return strat_id


def _upsert_pm_market(conn, ext_id: str, title: str,
                       resolution_time: Optional[str],
                       market_type: str = "moneyline") -> Optional[int]:
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
    """, (ext_id, title, market_type, resolution_time))
    row = cur.fetchone()
    return row[0] if row else None


def _already_traded(conn, strategy_id: int, market_db_id: int,
                    outcome: str) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM paper_trades
        WHERE strategy_id = %s AND market_id = %s
          AND result IS NULL
        LIMIT 1
    """, (strategy_id, market_db_id))
    return cur.fetchone() is not None


def _write_trade(conn, strategy_id: int, market_db_id: int, outcome: str,
                 entry_price: float, model_prob: float, edge_pp: float,
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
        model_prob, model_prob,
        json.dumps({"source": "NBA Elo Model", "elo_prob": round(model_prob, 4)}),
        round(edge_pp / 100, 6),
        confidence,
        STAKE_UNITS,
        reasoning,
    ))
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


def _log_run(conn, n_events: int, n_matched: int, n_edges: int, dry_run: bool):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_runs
            (run_type, started_at, finished_at, input_context, output_summary, status)
        VALUES ('nba_scanner:daily', NOW(), NOW(), %s::jsonb, %s, 'completed')
    """, (
        json.dumps({"events": n_events, "matched": n_matched,
                    "edges": n_edges, "dry_run": dry_run}),
        f"{'[DRY RUN] ' if dry_run else ''}Scanned {n_events} PM NBA events, "
        f"matched {n_matched}, found {n_edges} edges.",
    ))
    conn.commit()


# ── PM event fetching ────────────────────────────────────────────────────────

def _is_nba_game_event(event: dict) -> bool:
    """True if this is an individual NBA game (not futures, awards, draft)."""
    title = event.get("title", "")
    if re.search(r"champion|winner|mvp|draft|coach|award|all-nba|all-rookie|"
                 r"all-defensive|cover|retire|traded|leave|next team|parlay|"
                 r"total games|exact matchup|2k\d", title, re.I):
        return False
    # Must look like "Team A vs. Team B"
    if not re.search(r"vs\.?", title, re.I):
        return False
    return True


def _fetch_pm_events(days_ahead: int) -> list[dict]:
    """Fetch active NBA events from Polymarket.

    Uses tag_slug=nba to get all NBA markets (individual game markets
    don't always appear in date-filtered queries).
    """
    events: list[dict] = []
    page_size = 100
    for offset in range(0, 500, page_size):
        resp = requests.get(f"{GAMMA_API}/events", params={
            "closed": "false",
            "active": "true",
            "limit": page_size,
            "offset": offset,
            "tag_slug": "nba",
        }, timeout=15)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list) or not page:
            break
        events.extend(page)

    log.info(f"Fetched {len(events)} NBA-tagged PM events")
    return events


def _extract_teams(title: str) -> Optional[tuple[str, str]]:
    """Extract the two team names in title order from 'A vs. B'.

    NOTE: returns (first, second) as written — this is NOT (home, away).
    Polymarket titles are 'Away vs. Home', so the second team is the home
    side. Use _home_away_from_event() to get the correct home/away.
    """
    # Remove date suffixes, series info
    clean = re.sub(r"\s*\(.*?\)\s*$", "", title)
    clean = re.sub(r"\s*-\s*Game\s+\d+.*$", "", clean, flags=re.I)

    m = re.match(r"(.+?)\s+(?:vs?\.?|versus)\s+(.+?)$", clean, re.I)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def _home_away_from_event(event: dict) -> Optional[tuple[str, str]]:
    """Return (home_raw, away_raw) using Polymarket's OWN home/away designation.

    The home team is whoever actually hosts the game — it gets the Elo home
    advantage. Getting this wrong inverts the model (a +100 Elo swing). Priority:
      1) event['teams'][].ordering == 'home'/'away'   (authoritative PM metadata)
      2) slug 'nba-{away}-{home}-YYYY-MM-DD'           (abbreviations)
      3) title 'Away vs. Home' → second team is home   (last-resort fallback)
    """
    # 1) Explicit teams array with an 'ordering' flag.
    teams = event.get("teams")
    if isinstance(teams, list) and len(teams) == 2:
        def _name(t):
            return t.get("name") or t.get("alias") or t.get("abbreviation")
        home_t = next((t for t in teams
                       if str(t.get("ordering", "")).lower() == "home"), None)
        away_t = next((t for t in teams
                       if str(t.get("ordering", "")).lower() == "away"), None)
        if home_t and away_t and _name(home_t) and _name(away_t):
            return _name(home_t), _name(away_t)

    # 2) Slug encodes away-then-home: nba-{away}-{home}-YYYY-MM-DD
    m = re.match(r"^nba-([a-z]{2,4})-([a-z]{2,4})-\d{4}-\d{2}-\d{2}",
                 event.get("slug", "") or "")
    if m:
        away_ab, home_ab = m.group(1), m.group(2)
        return home_ab, away_ab

    # 3) Title fallback — PM convention is 'Away vs. Home', so second = home.
    parsed = _extract_teams(event.get("title", ""))
    if parsed:
        first, second = parsed
        return second, first

    return None


# ── Playoff context adjustments ──────────────────────────────────────────────

def _get_playoff_context(conn, home: str, away: str) -> dict:
    """Query recent games between these teams to detect playoff series context."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT m.kickoff_utc, m.home_score, m.away_score,
               ht.canonical_name AS home_team,
               at.canonical_name AS away_team
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN seasons s ON s.id = m.season_id
        JOIN leagues l ON l.id = s.league_id
        WHERE l.code = 'USA-NBA'
          AND m.status = 'finished'
          AND (
              (ht.canonical_name = %s AND at.canonical_name = %s)
              OR (ht.canonical_name = %s AND at.canonical_name = %s)
          )
          AND m.kickoff_utc > NOW() - INTERVAL '30 days'
        ORDER BY m.kickoff_utc ASC
    """, (home, away, away, home))

    games = cur.fetchall()
    if not games:
        return {"is_playoff_series": False, "games_played": 0}

    # Count wins for each team in recent matchups
    home_wins = 0
    away_wins = 0
    margins = []
    for g in games:
        if g["home_team"] == home:
            if g["home_score"] > g["away_score"]:
                home_wins += 1
            else:
                away_wins += 1
            margins.append(g["home_score"] - g["away_score"])
        else:
            if g["away_score"] > g["home_score"]:
                home_wins += 1
            else:
                away_wins += 1
            margins.append(g["away_score"] - g["home_score"])

    n_games = len(games)
    is_playoff = n_games >= 2  # Multiple recent H2H = playoff series

    last_margin = margins[-1] if margins else 0
    blowout_last = abs(last_margin) >= 20

    return {
        "is_playoff_series": is_playoff,
        "games_played": n_games,
        "home_series_wins": home_wins,
        "away_series_wins": away_wins,
        "last_margin": last_margin,
        "blowout_last_game": blowout_last,
        "is_potential_closeout": home_wins == 3 or away_wins == 3,
        "is_game_7": home_wins == 3 and away_wins == 3,
    }


def _apply_playoff_adjustments(base_prob: float, ctx: dict,
                                is_home_side: bool) -> float:
    """Apply contextual adjustments to Elo probability for playoff games."""
    adj = base_prob

    if not ctx.get("is_playoff_series"):
        return adj

    # Game 7 home boost: historically ~65-70% for home team
    if ctx.get("is_game_7") and is_home_side:
        adj = adj * 1.05  # 5% boost to home in G7

    # Closeout game: team with 3 wins has ~65% chance
    if ctx.get("is_potential_closeout"):
        if is_home_side and ctx["home_series_wins"] == 3:
            adj = adj * 1.04
        elif not is_home_side and ctx["away_series_wins"] == 3:
            adj = adj * 1.04

    # Post-blowout momentum: team that won big has momentum
    if ctx.get("blowout_last_game"):
        if ctx["last_margin"] > 0 and is_home_side:
            adj = adj * 1.03  # home team blew out, momentum
        elif ctx["last_margin"] < 0 and not is_home_side:
            adj = adj * 1.03  # away team blew out, momentum

    return min(adj, 0.95)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(days_ahead: int = DEFAULT_DAYS_AHEAD,
        threshold_pp: float = DEFAULT_EDGE_THRESHOLD_PP,
        dry_run: bool = False) -> dict:

    log.info(f"Loading NBA Elo model from {ELO_PATH}...")
    elo = NBAElo.load(ELO_PATH)
    log.info(f"  {len(elo.ratings)} teams loaded")

    log.info(f"Fetching PM events (next {days_ahead} days)...")
    events = _fetch_pm_events(days_ahead)

    conn = None if dry_run else _conn()

    try:
        strategy_id = None if dry_run else _get_or_create_strategy(conn)

        n_nba = 0
        n_matched = 0
        n_edges = 0
        trades_logged = []

        for event in events:
            title = event.get("title", "")
            end_date = event.get("endDate", "")

            if not _is_nba_game_event(event):
                continue
            n_nba += 1

            ha = _home_away_from_event(event)
            if not ha:
                log.debug(f"  Could not parse teams from: {title}")
                continue

            home_raw, away_raw = ha
            home = _resolve_team(home_raw)
            away = _resolve_team(away_raw)

            if not home or not away:
                log.debug(f"  Unresolved: {home_raw}={home}, {away_raw}={away}")
                continue

            if home not in elo.ratings or away not in elo.ratings:
                log.debug(f"  Not in Elo: {home} or {away}")
                continue

            # Base Elo prediction
            pred = elo.predict(home, away)
            n_matched += 1

            # Playoff context
            ctx = {} if dry_run else _get_playoff_context(conn, home, away)

            # Scan ALL markets — collect candidates, write only the best per event
            home_elo_val = elo.ratings.get(home, 1500)
            away_elo_val = elo.ratings.get(away, 1500)
            home_elo = round(home_elo_val, 0)
            away_elo = round(away_elo_val, 0)
            exp_margin = _expected_margin(home_elo_val, away_elo_val)

            # Team scoring stats for totals (lazy-loaded)
            _home_stats = None
            _away_stats = None
            def _get_scoring():
                nonlocal _home_stats, _away_stats
                if _home_stats is None and not dry_run:
                    _home_stats = _get_team_scoring_stats(conn, home) or {}
                    _away_stats = _get_team_scoring_stats(conn, away) or {}
                return _home_stats, _away_stats

            candidates: list[dict] = []

            for mkt in event.get("markets", []):
                if not mkt.get("active") or mkt.get("closed"):
                    continue

                question = mkt.get("question", "")
                classified = _classify_nba_market(question)
                if not classified:
                    continue

                mtype = classified["type"]

                raw = mkt.get("outcomePrices") or mkt.get("outcome_prices")
                if not raw:
                    continue
                prices = json.loads(raw) if isinstance(raw, str) else raw
                yes_p = float(prices[0])
                if yes_p <= 0.03 or yes_p >= 0.97:
                    continue

                model_prob = None
                outcome_label = ""
                mkt_type_label = mtype

                # ── MONEYLINE / 1H MONEYLINE ──
                if mtype in ("moneyline", "1h_moneyline"):
                    q_teams = _extract_teams(question.split(":")[0])
                    if not q_teams:
                        continue
                    q_first_resolved = _resolve_team(q_teams[0])
                    if q_first_resolved == home:
                        yes_team = "home"
                    elif q_first_resolved == away:
                        yes_team = "away"
                    else:
                        continue

                    if mtype == "1h_moneyline":
                        # 1H moneyline: regress win prob toward 50%
                        base = pred["home_win"] if yes_team == "home" else pred["away_win"]
                        model_prob = 0.5 + (base - 0.5) * 0.75
                    else:
                        base = pred["home_win"] if yes_team == "home" else pred["away_win"]
                        model_prob = _apply_playoff_adjustments(
                            base, ctx, is_home_side=(yes_team == "home"))
                    outcome_label = f"{yes_team.upper()}_ML"

                # ── SPREAD / 1H SPREAD ──
                elif mtype in ("spread", "1h_spread"):
                    spread_team_name = classified["team"]
                    spread_line = classified["line"]
                    spread_team = _resolve_team(spread_team_name)
                    if not spread_team or spread_team not in (home, away):
                        continue

                    # Expected margin from spread team's perspective
                    if spread_team == home:
                        team_margin = exp_margin
                    else:
                        team_margin = -exp_margin

                    is_half = (mtype == "1h_spread")
                    model_prob = _spread_cover_prob(
                        team_margin, spread_line, is_half=is_half)
                    sign = "+" if spread_line >= 0 else ""
                    outcome_label = f"{'1H ' if is_half else ''}SPREAD {spread_team_name} ({sign}{spread_line})"

                # ── TOTAL (O/U) / 1H TOTAL ──
                elif mtype in ("total", "1h_total"):
                    total_line = classified["line"]
                    is_half = (mtype == "1h_total")

                    hs, aws = _get_scoring()
                    if hs and aws and hs.get("avg_scored") and aws.get("avg_scored"):
                        # Matchup-adjusted: home offense vs away defense + vice versa
                        home_off = (hs["avg_scored"] + aws["avg_allowed"]) / 2
                        away_off = (aws["avg_scored"] + hs["avg_allowed"]) / 2
                        expected_total = home_off + away_off
                    else:
                        expected_total = LEAGUE_AVG_TOTAL

                    model_prob = _total_over_prob(
                        expected_total, total_line, is_half=is_half)
                    outcome_label = f"{'1H ' if is_half else ''}OVER {total_line}"

                if model_prob is None:
                    continue

                # Check both YES and NO sides for edge
                edge_pp = round((model_prob - yes_p) * 100, 1)
                no_p = 1.0 - yes_p
                no_prob = 1.0 - model_prob
                no_edge_pp = round((no_prob - no_p) * 100, 1)

                if edge_pp >= threshold_pp:
                    side, entry, fair, edge = "YES", yes_p, model_prob, edge_pp
                    outcome = outcome_label
                elif no_edge_pp >= threshold_pp:
                    side, entry, fair, edge = "NO", no_p, no_prob, no_edge_pp
                    if mtype in ("total", "1h_total"):
                        outcome = outcome_label.replace("OVER", "UNDER")
                    elif mtype in ("moneyline", "1h_moneyline"):
                        opposite = "AWAY" if "HOME" in outcome_label else "HOME"
                        outcome = f"{opposite}_ML"
                    else:
                        outcome = f"NO_{outcome_label}"
                else:
                    continue

                reasoning = (
                    f"NBA Elo [{mkt_type_label}]: {home} ({home_elo}) vs "
                    f"{away} ({away_elo}). {question} — {side} side. "
                    f"PM: {entry*100:.1f}% ({1/entry:.2f}). "
                    f"Model: {fair*100:.1f}% ({1/fair:.2f}). "
                    f"Edge: +{edge:.1f}pp."
                )
                if mtype in ("spread", "1h_spread"):
                    reasoning += f" Exp margin: {exp_margin:+.1f}."
                if mtype in ("total", "1h_total"):
                    hs, aws = _get_scoring()
                    if hs and aws and hs.get("avg_scored") and aws.get("avg_scored"):
                        exp_t = (hs["avg_scored"] + aws["avg_allowed"]) / 2
                        exp_t += (aws["avg_scored"] + hs["avg_allowed"]) / 2
                        reasoning += f" Exp total: {exp_t:.1f}."
                if ctx.get("is_playoff_series"):
                    reasoning += (
                        f" Playoff: {ctx['home_series_wins']}-"
                        f"{ctx['away_series_wins']}."
                    )
                    if ctx.get("is_game_7"):
                        reasoning += " GAME 7."

                candidates.append({
                    "mkt": mkt, "outcome": outcome, "side": side,
                    "entry": entry, "fair": fair, "edge": edge,
                    "reasoning": reasoning, "mtype": mkt_type_label,
                })

            # Log the best edge per market-TYPE group (moneyline / spread / total
            # + 1H variants). Paper-only, so we keep every bet type that shows an
            # edge to build a richer dataset — but PM lists ~25 alternate spread and
            # ~25 alternate total lines per game, so we collapse each type to its
            # single best edge rather than flooding the table with correlated lines.
            if candidates:
                best_by_type: dict[str, dict] = {}
                for c in candidates:
                    g = c["mtype"]
                    if g not in best_by_type or c["edge"] > best_by_type[g]["edge"]:
                        best_by_type[g] = c

                for best in sorted(best_by_type.values(),
                                   key=lambda c: c["edge"], reverse=True):
                    n_edges += 1

                    log.info(
                        f"  EDGE +{best['edge']:.1f}pp | {home} vs {away} | "
                        f"{best['mtype']} → {best['outcome']} ({best['side']}) | "
                        f"PM={best['entry']*100:.1f}% Model={best['fair']*100:.1f}% | "
                        f"Elo {home_elo:.0f} vs {away_elo:.0f}"
                    )

                    if not dry_run:
                        ext_id = str(best["mkt"].get("id") or
                                    best["mkt"].get("conditionId") or "")
                        question = best["mkt"].get("question", "")
                        market_db_id = _upsert_pm_market(
                            conn, ext_id, question, end_date,
                            market_type=best["mtype"])
                        if market_db_id:
                            trade_id = _write_trade(
                                conn, strategy_id, market_db_id,
                                best["outcome"], best["entry"], best["fair"],
                                best["edge"], best["reasoning"],
                            )
                            if trade_id:
                                log.info(f"    → Trade #{trade_id} logged")
                                trades_logged.append(trade_id)
                            else:
                                log.info(f"    → Already traded, skipping")

        if not dry_run and conn:
            _log_run(conn, n_nba, n_matched, n_edges, dry_run)

        log.info(
            f"\nSummary: {n_nba} NBA events, {n_matched} matched to Elo, "
            f"{n_edges} edges found, {len(trades_logged)} trades logged"
        )

        return {
            "nba_events": n_nba,
            "matched": n_matched,
            "edges": n_edges,
            "trades": len(trades_logged),
        }

    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="NBA Elo Scanner")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS_AHEAD)
    parser.add_argument("--threshold", type=float, default=DEFAULT_EDGE_THRESHOLD_PP)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(days_ahead=args.days, threshold_pp=args.threshold, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
