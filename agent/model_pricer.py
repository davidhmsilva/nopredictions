"""
Model Pricer — pre-match fair probabilities from our own historical model.

Uses a simplified Poisson attack/defense model calibrated on the last N
matches from the DB.  Falls back to ClubElo win probability when team
history is insufficient.

Returns a sharp_lookup dict in the same format as paper_trader.fetch_sharp_odds_today(),
so it can be used as a drop-in replacement.

Algorithm (Poisson form model):
  1. Pull the last FORM_MATCHES_HOME home matches for the home team
     and the last FORM_MATCHES_AWAY away matches for the away team.
  2. Compute attack_strength = team_avg_goals_scored / league_baseline
     and defense_weakness    = team_avg_goals_conceded / league_baseline.
  3. Lambda_home = LEAGUE_HOME_AVG * home_attack_strength * away_defense_weakness
     Lambda_away = LEAGUE_AWAY_AVG * away_attack_strength * home_defense_weakness
  4. 1X2 probabilities via Poisson convolution matrix (same math as poisson_trader).

ClubElo fallback:
  When a team has < MIN_FORM_MATCHES results in the DB, use the Elo difference
  to estimate win probabilities via the standard logistic formula.

Edge confidence:
  Model-based edges are less reliable than Pinnacle/Betfair sharp consensus.
  A higher threshold (MODEL_EDGE_THRESHOLD_PP) is applied inside paper_trader
  when this module is the source.  Each edge is labelled 'poisson_form_model'
  or 'clubelo' in the sources dict so the website can show the provenance.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from scipy.stats import poisson

log = logging.getLogger(__name__)

# ── Model constants ────────────────────────────────────────────────────────────

FORM_MATCHES_HOME   = 15    # recent home matches for home-side calibration
FORM_MATCHES_AWAY   = 15    # recent away matches for away-side calibration
MIN_FORM_MATCHES    = 5     # minimum matches to attempt the Poisson model
LEAGUE_HOME_AVG     = 1.45  # European football baseline expected home goals
LEAGUE_AWAY_AVG     = 1.20  # European football baseline expected away goals
MAX_GOALS           = 8     # max goals in Poisson matrix

# ClubElo parameters
ELO_K               = 400   # win-probability scale factor
HOME_ELO_BONUS      = 65    # Elo points added to home team to capture home advantage

# Source labels (appear in paper_trade.sharp_consensus_sources)
SOURCE_POISSON  = 'poisson_form_model'
SOURCE_CLUBELO  = 'clubelo'


# ── Team name normalisation (mirrors paper_trader._norm) ─────────────────────

_STRIP_WORDS = r'\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia|club|de|da|do|dos|la|el|al|cd|ca|cs|ssc)\b'

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(_STRIP_WORDS, '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _pg_norm(col: str) -> str:
    """SQL expression to normalise a column like _norm() does in Python."""
    return f"TRIM(REGEXP_REPLACE(LOWER({col}), '[^a-z0-9 ]+', ' ', 'g'))"


# ── DB: team lookup ───────────────────────────────────────────────────────────

def find_team_id(conn, name: str) -> Optional[int]:
    """
    Match a free-text team name to a canonical team_id.

    Tries in order:
      1. Exact normalised canonical_name match
      2. Exact normalised alias match
      3. Canonical_name LIKE '%prefix8%'  (substring)
      4. Any alias LIKE '%prefix8%'        (substring)

    Returns team_id (int) or None if no match found.
    """
    norm  = _norm(name)
    pre8  = norm[:8]
    cur   = conn.cursor()

    # 1. Exact canonical
    cur.execute(
        f"SELECT id FROM teams WHERE {_pg_norm('canonical_name')} = %s LIMIT 1",
        (norm,)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 2. Exact alias
    cur.execute(
        f"SELECT team_id FROM team_aliases WHERE {_pg_norm('alias')} = %s LIMIT 1",
        (norm,)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 3. Canonical substring
    cur.execute(
        "SELECT id FROM teams WHERE LOWER(canonical_name) LIKE %s "
        "ORDER BY LENGTH(canonical_name) LIMIT 1",
        (f'%{pre8}%',)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 4. Alias substring
    cur.execute(
        "SELECT team_id FROM team_aliases WHERE LOWER(alias) LIKE %s "
        "ORDER BY LENGTH(alias) LIMIT 1",
        (f'%{pre8}%',)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    return None


# ── DB: recent form ───────────────────────────────────────────────────────────

def _home_form(conn, team_id: int, n: int) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            m.home_score AS goals_for,
            m.away_score AS goals_against,
            ms.home_xg   AS xg_for,
            ms.away_xg   AS xg_against
        FROM matches m
        LEFT JOIN match_stats ms ON ms.match_id = m.id
        WHERE m.home_team_id = %s
          AND m.home_score IS NOT NULL
          AND m.kickoff_utc < NOW()
        ORDER BY m.kickoff_utc DESC
        LIMIT %s
    """, (team_id, n))
    return [dict(r) for r in cur.fetchall()]


def _away_form(conn, team_id: int, n: int) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            m.away_score AS goals_for,
            m.home_score AS goals_against,
            ms.away_xg   AS xg_for,
            ms.home_xg   AS xg_against
        FROM matches m
        LEFT JOIN match_stats ms ON ms.match_id = m.id
        WHERE m.away_team_id = %s
          AND m.home_score IS NOT NULL
          AND m.kickoff_utc < NOW()
        ORDER BY m.kickoff_utc DESC
        LIMIT %s
    """, (team_id, n))
    return [dict(r) for r in cur.fetchall()]


def _avg_goals(form: list[dict], prefer_xg: bool = True) -> tuple[float, float]:
    """
    Average goals_for and goals_against from a form list.
    Uses xG when available in ≥ 30 % of matches; otherwise raw goals.
    Returns (avg_for, avg_against).
    """
    if not form:
        return (LEAGUE_HOME_AVG, LEAGUE_HOME_AVG)  # neutral fallback

    xg_count = sum(1 for r in form if r.get('xg_for') is not None)
    use_xg = prefer_xg and (xg_count / len(form) >= 0.30)

    if use_xg:
        gf = [float(r['xg_for'])      for r in form if r.get('xg_for')      is not None]
        ga = [float(r['xg_against'])  for r in form if r.get('xg_against')  is not None]
    else:
        gf = [float(r['goals_for'])     for r in form if r.get('goals_for')     is not None]
        ga = [float(r['goals_against']) for r in form if r.get('goals_against') is not None]

    avg_for     = sum(gf) / len(gf) if gf else LEAGUE_HOME_AVG
    avg_against = sum(ga) / len(ga) if ga else LEAGUE_HOME_AVG
    return avg_for, avg_against


# ── Poisson 1X2 matrix ────────────────────────────────────────────────────────

def _poisson_probs(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    p_home = p_draw = p_away = 0.0
    for i in range(MAX_GOALS + 1):
        pi = poisson.pmf(i, lam_h)
        for j in range(MAX_GOALS + 1):
            pij = pi * poisson.pmf(j, lam_a)
            if i > j:    p_home += pij
            elif i == j: p_draw += pij
            else:         p_away += pij
    return p_home, p_draw, p_away


# ── ClubElo fallback ──────────────────────────────────────────────────────────

def _get_elo(conn, team_name: str, match_date: date | None = None) -> Optional[float]:
    """Look up ClubElo rating for `team_name` on `match_date`."""
    if match_date is None:
        match_date = date.today()
    norm = _norm(team_name)
    pre8 = norm[:8]
    cur  = conn.cursor()
    cur.execute("""
        SELECT elo FROM club_elo
        WHERE LOWER(REGEXP_REPLACE(club_name, '[^a-z0-9 ]', ' ', 'g')) LIKE %s
          AND from_date <= %s
          AND to_date   >= %s
        ORDER BY to_date DESC
        LIMIT 1
    """, (f'%{pre8}%', match_date, match_date))
    row = cur.fetchone()
    return float(row[0]) if row else None


def _elo_probs(elo_home: float, elo_away: float) -> tuple[float, float, float]:
    """
    Convert Elo ratings → 1X2 probabilities.
    Uses a Gaussian draw model: draw peaks at ~27% when teams are equal
    and decays exponentially with the Elo difference.
    """
    diff       = (elo_home + HOME_ELO_BONUS) - elo_away
    p_win_raw  = 1.0 / (1.0 + 10.0 ** (-diff / ELO_K))
    draw_prob  = max(0.05, min(0.32, 0.27 * math.exp(-0.0002 * diff ** 2)))
    remaining  = 1.0 - draw_prob
    return p_win_raw * remaining, draw_prob, (1.0 - p_win_raw) * remaining


# ── Core: price a single match ────────────────────────────────────────────────

def price_match(conn, home_name: str, away_name: str) -> Optional[dict]:
    """
    Generate fair 1X2 probabilities for a match.

    Returns a dict with keys:
      home_prob, draw_prob, away_prob,
      method ('poisson_form' | 'clubelo'),
      + model-specific diagnostics (lam_home, lam_away, elo_home, elo_away, …)

    Returns None if we have no data for either team.
    """
    home_id = find_team_id(conn, home_name)
    away_id = find_team_id(conn, away_name)

    if home_id and away_id:
        home_form = _home_form(conn, home_id, FORM_MATCHES_HOME)
        away_form = _away_form(conn, away_id, FORM_MATCHES_AWAY)

        if len(home_form) >= MIN_FORM_MATCHES and len(away_form) >= MIN_FORM_MATCHES:
            # Compute averages from venue-specific form
            home_att_avg, home_def_avg = _avg_goals(home_form)
            away_att_avg, away_def_avg = _avg_goals(away_form)

            # Strength relative to league baseline
            home_att  = max(0.3, min(home_att_avg  / LEAGUE_HOME_AVG, 3.0))
            home_def  = max(0.3, min(home_def_avg  / LEAGUE_AWAY_AVG, 3.0))
            away_att  = max(0.3, min(away_att_avg  / LEAGUE_AWAY_AVG, 3.0))
            away_def  = max(0.3, min(away_def_avg  / LEAGUE_HOME_AVG, 3.0))

            lam_h = max(0.3, min(LEAGUE_HOME_AVG * home_att * away_def, 5.0))
            lam_a = max(0.3, min(LEAGUE_AWAY_AVG * away_att * home_def, 5.0))

            ph, pd, pa = _poisson_probs(lam_h, lam_a)

            return {
                'home_prob':        ph,
                'draw_prob':        pd,
                'away_prob':        pa,
                'method':           'poisson_form',
                'lam_home':         round(lam_h, 4),
                'lam_away':         round(lam_a, 4),
                'home_att_avg':     round(home_att_avg, 3),
                'away_att_avg':     round(away_att_avg, 3),
                'home_form_n':      len(home_form),
                'away_form_n':      len(away_form),
            }

    # ── ClubElo fallback ──────────────────────────────────────────────────────
    elo_h = _get_elo(conn, home_name)
    elo_a = _get_elo(conn, away_name)

    if elo_h and elo_a:
        ph, pd, pa = _elo_probs(elo_h, elo_a)
        return {
            'home_prob':  ph,
            'draw_prob':  pd,
            'away_prob':  pa,
            'method':     'clubelo',
            'elo_home':   elo_h,
            'elo_away':   elo_a,
        }

    log.debug(f'[model_pricer] No data for {home_name} vs {away_name}')
    return None


# ── Build lookup — drop-in for fetch_sharp_odds_today() ───────────────────────

def build_lookup(conn, pm_markets: list[dict]) -> dict[str, dict]:
    """
    Build a sharp_lookup dict from PM markets using our own model.
    Format is identical to paper_trader.fetch_sharp_odds_today() output.

    Called from paper_trader.run() when Odds API + DB fallback both fail.
    """
    from .paper_trader import _match_keys

    lookup:      dict[str, dict] = {}
    priced       = 0
    pairs_seen:  set[tuple]      = set()

    for mkt in pm_markets:
        home = mkt.get('_home_team')
        away = mkt.get('_away_team')
        if not home or not away:
            continue
        pair = (home, away)
        if pair in pairs_seen:
            continue
        pairs_seen.add(pair)

        result = price_match(conn, home, away)
        if result is None:
            continue

        method_key = SOURCE_POISSON if result['method'] == 'poisson_form' else SOURCE_CLUBELO

        event: dict = {
            'home':       home,
            'away':       away,
            'home_prob':  result['home_prob'],
            'draw_prob':  result['draw_prob'],
            'away_prob':  result['away_prob'],
            'sources':    {method_key: result},
            'method':     result['method'],
        }

        for key in _match_keys(home, away):
            lookup[key] = event
        priced += 1

        log.info(
            f'[model_pricer] {home} vs {away}: '
            f'H={result["home_prob"]:.3f} D={result.get("draw_prob",0):.3f} '
            f'A={result["away_prob"]:.3f}  [{result["method"]}]'
        )

    log.info(f'[model_pricer] Priced {priced}/{len(pairs_seen)} matches')
    return lookup
