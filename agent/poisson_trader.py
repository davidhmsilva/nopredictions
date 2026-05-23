"""
Poisson In-Play Trader v2 — dynamic in-play model with DC lambdas.

Upgrades over v1:
- DC model lambdas as primary source (no Odds API needed)
- Red card adjustment: 10-man team gets lambda penalty, opponent gets boost
- Trailing-team push after 70': losing team attacks harder, draw becomes less likely
- Goal-driven lambda recalculation: each goal shifts expected intensity
- Non-linear time model: stoppage time awareness, half-time adjustment
- Richer live data from api-football.com (red cards, shots on target)

Usage:
    from agent import poisson_trader
    trades = poisson_trader.run()               # scan live matches
    trades = poisson_trader.run(dry_run=True)    # print only
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import poisson as poisson_dist
from scipy.optimize import minimize

from . import paper_trader
from .paper_trader import (
    fetch_pm_markets_today, _norm, _match_keys,
    _fuzzy_find_event_two, _extract_home_away_from_event_title,
    _conn, _serial, EDGE_THRESHOLD_PP, STAKE_UNITS, GAMMA_API, _get,
)
from .tools.db import log_agent_run
from .live_odds import LiveOddsTracker, get_pm_vs_bookmaker_edge
from .injury_tracker import InjuryTracker
from .market_flow import MarketFlow

log = logging.getLogger(__name__)

INPLAY_EDGE_THRESHOLD_PP = 5.0
MAX_GOALS = 8
WHALE_VOLUME_USDC = 10000  # threshold for significant whale activity

# ─── DC model loader ────────────────────────────────────────────────────────

_dc_model = None

def _load_dc_model():
    global _dc_model
    if _dc_model is not None:
        return _dc_model
    try:
        from .dixon_coles import DixonColesModel
        params_path = Path(__file__).parent / 'dc_model_params.json'
        if params_path.exists():
            _dc_model = DixonColesModel.load(str(params_path))
            log.info(f'[poisson] DC model loaded: {_dc_model.n_matches} matches, {len(_dc_model.teams)} teams')
        else:
            log.warning('[poisson] dc_model_params.json not found')
    except Exception as e:
        log.warning(f'[poisson] Failed to load DC model: {e}')
    return _dc_model


# ─── Poisson math ────────────────────────────────────────────────────────────

def _poisson_match_probs(lam_h: float, lam_a: float) -> tuple[float, float, float]:
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
    return p_home, p_draw, p_away


def derive_lambdas(p_home: float, p_draw: float, p_away: float) -> tuple[float, float]:
    """
    Given sharp 1x2 probabilities, solve for (lambda_h, lambda_a) that
    best reproduce them via independent Poisson.
    """
    def objective(params):
        lh, la = params
        if lh <= 0.01 or la <= 0.01:
            return 1e6
        ph, pd, pa = _poisson_match_probs(lh, la)
        return (ph - p_home) ** 2 + (pd - p_draw) ** 2 + (pa - p_away) ** 2

    result = minimize(objective, x0=[1.4, 1.1], method='Nelder-Mead',
                      options={'xatol': 1e-5, 'fatol': 1e-8, 'maxiter': 500})
    lam_h, lam_a = max(0.05, result.x[0]), max(0.05, result.x[1])
    return lam_h, lam_a


# ─── Dynamic in-play adjustments ─────────────────────────────────────────────

RED_CARD_LAMBDA_PENALTY = 0.80    # 10-man team scores at 80% of base rate
RED_CARD_LAMBDA_BOOST = 1.10      # opponent scores at 110% against 10 men
TRAILING_PUSH_MINUTE = 70         # when trailing-team boost kicks in
# Aggressive trailing push: 1 goal down = 1.25x, 2+ down = 1.40x
TRAILING_PUSH_FACTOR_1G = 1.25    # trailing by 1 attacks 25% harder after 70'
TRAILING_PUSH_FACTOR_2G = 1.40    # trailing by 2+ attacks 40% harder after 80'
TRAILING_DRAW_SQUEEZE = 0.90      # draw becomes 10% less likely when team pushes

# Goal-driven intensity shift: each goal changes momentum
GOAL_MOMENTUM_LEADING = 0.92     # team that scores tends to sit back slightly
GOAL_MOMENTUM_TRAILING = 1.08    # team that concedes pushes harder

# Sit-back adjustments: leading team parks the bus
LEADING_DEFENSIVE_1G = 0.75      # leading by 1 after 85' = 0.75x lambda
LEADING_DEFENSIVE_2G = 0.65      # leading by 2+ after 85' = 0.65x lambda


def _effective_minute(minute: int) -> float:
    """
    Convert match minute to effective remaining fraction.
    Non-linear: accounts for half-time break and stoppage time.
    """
    if minute <= 0:
        return 1.0
    if minute >= 90:
        # Stoppage time: goals still happen but at reduced rate
        # ~3 min stoppage on average, model as decaying tail
        extra = minute - 90
        return max(0.0, 3.0 / 90.0 * math.exp(-extra / 3.0))
    if 45 <= minute <= 47:
        # Half-time: treat as minute 45
        return (90 - 45) / 90.0
    return (90 - minute) / 90.0


def _adjust_lambdas_for_red_cards(
    lam_h: float, lam_a: float,
    home_reds: int, away_reds: int
) -> tuple[float, float]:
    """Adjust lambdas when a team has red cards."""
    adj_h = lam_h
    adj_a = lam_a

    for _ in range(home_reds):
        adj_h *= RED_CARD_LAMBDA_PENALTY
        adj_a *= RED_CARD_LAMBDA_BOOST

    for _ in range(away_reds):
        adj_a *= RED_CARD_LAMBDA_PENALTY
        adj_h *= RED_CARD_LAMBDA_BOOST

    return adj_h, adj_a


def _adjust_lambdas_for_score(
    lam_h: float, lam_a: float,
    home_goals: int, away_goals: int,
    minute: int
) -> tuple[float, float]:
    """
    Adjust lambdas based on current score and game phase.
    - Trailing team: aggressive push after 70', scale by goal deficit
    - Leading team: sits back (especially if 2+ up)
    """
    adj_h = lam_h
    adj_a = lam_a

    goal_diff = home_goals - away_goals

    if goal_diff != 0:
        # Goal momentum: leading team defends, trailing team attacks
        if goal_diff > 0:
            # Home leading
            adj_h *= GOAL_MOMENTUM_LEADING
            adj_a *= GOAL_MOMENTUM_TRAILING
        else:
            # Away leading
            adj_a *= GOAL_MOMENTUM_LEADING
            adj_h *= GOAL_MOMENTUM_TRAILING

        # Late-game trailing-team push (aggressive)
        if minute >= TRAILING_PUSH_MINUTE:
            progress = min(1.0, (minute - TRAILING_PUSH_MINUTE) / 20.0)
            # Scale push by goal deficit
            if abs(goal_diff) == 1:
                push = 1.0 + (TRAILING_PUSH_FACTOR_1G - 1.0) * progress
            else:  # 2+
                push = 1.0 + (TRAILING_PUSH_FACTOR_2G - 1.0) * progress

            if goal_diff > 0:
                adj_a *= push
            else:
                adj_h *= push

        # Late-game sit-back: leading team parks the bus
        if minute >= 85:
            progress = min(1.0, (minute - 85) / 5.0)
            if goal_diff > 0:
                # Home leading
                sit_back = 1.0 - (1.0 - (LEADING_DEFENSIVE_1G if abs(goal_diff) == 1 else LEADING_DEFENSIVE_2G)) * progress
                adj_h *= sit_back
            else:
                # Away leading
                sit_back = 1.0 - (1.0 - (LEADING_DEFENSIVE_1G if abs(goal_diff) == 1 else LEADING_DEFENSIVE_2G)) * progress
                adj_a *= sit_back

    return adj_h, adj_a


def inplay_probs(
    lam_h: float, lam_a: float,
    home_goals: int, away_goals: int,
    minute: int,
    home_reds: int = 0, away_reds: int = 0,
) -> dict[str, float]:
    """
    Dynamic in-play probabilities with all adjustments applied.
    """
    # 1. Red card adjustment
    lam_h, lam_a = _adjust_lambdas_for_red_cards(lam_h, lam_a, home_reds, away_reds)

    # 2. Score-driven momentum adjustment
    lam_h, lam_a = _adjust_lambdas_for_score(lam_h, lam_a, home_goals, away_goals, minute)

    # 3. Time remaining (non-linear)
    remaining = _effective_minute(minute)
    lam_h_rem = lam_h * remaining
    lam_a_rem = lam_a * remaining

    # 4. Calculate outcome probabilities from remaining-time Poisson
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    total_goals_dist = {}

    for k in range(MAX_GOALS + 1):
        pk = poisson_dist.pmf(k, lam_h_rem) if lam_h_rem > 0 else (1.0 if k == 0 else 0.0)
        for j in range(MAX_GOALS + 1):
            pj = poisson_dist.pmf(j, lam_a_rem) if lam_a_rem > 0 else (1.0 if j == 0 else 0.0)
            p_kj = pk * pj
            final_h = home_goals + k
            final_a = away_goals + j
            if final_h > final_a:
                p_home += p_kj
            elif final_h == final_a:
                p_draw += p_kj
            else:
                p_away += p_kj

            total_final = final_h + final_a
            total_goals_dist[total_final] = total_goals_dist.get(total_final, 0.0) + p_kj

    total = p_home + p_draw + p_away
    if total > 0:
        p_home /= total
        p_draw /= total
        p_away /= total
        for k in total_goals_dist:
            total_goals_dist[k] /= total

    # O/U and BTTS from distribution
    over_2_5 = sum(p for g, p in total_goals_dist.items() if g > 2.5)
    under_2_5 = sum(p for g, p in total_goals_dist.items() if g <= 2.5)
    over_1_5 = sum(p for g, p in total_goals_dist.items() if g > 1.5)
    under_1_5 = sum(p for g, p in total_goals_dist.items() if g <= 1.5)

    # BTTS: at least 1 goal each in final score
    btts = 0.0
    for k in range(MAX_GOALS + 1):
        pk = poisson_dist.pmf(k, lam_h_rem) if lam_h_rem > 0 else (1.0 if k == 0 else 0.0)
        for j in range(MAX_GOALS + 1):
            pj = poisson_dist.pmf(j, lam_a_rem) if lam_a_rem > 0 else (1.0 if j == 0 else 0.0)
            if (home_goals + k) >= 1 and (away_goals + j) >= 1:
                btts += pk * pj
    if total > 0:
        btts /= total

    # Spread distribution: using normal approximation (CDF) with stdev ~16.5 for single match
    # Spread 0.5 = home score - away score > 0.5
    spread_dist = {}
    spread_stdev = 16.5  # empirical from PM spread odds
    for k in range(MAX_GOALS + 1):
        pk = poisson_dist.pmf(k, lam_h_rem) if lam_h_rem > 0 else (1.0 if k == 0 else 0.0)
        for j in range(MAX_GOALS + 1):
            pj = poisson_dist.pmf(j, lam_a_rem) if lam_a_rem > 0 else (1.0 if j == 0 else 0.0)
            spread = (home_goals + k) - (away_goals + j)
            spread_dist[spread] = spread_dist.get(spread, 0.0) + pk * pj

    # Common spread lines: H -0.5, -1.5, +0.5, +1.5
    spread_0_5_home = sum(p for s, p in spread_dist.items() if s > 0.5)
    spread_1_5_home = sum(p for s, p in spread_dist.items() if s > 1.5)
    spread_0_5_away = sum(p for s, p in spread_dist.items() if s < -0.5)
    spread_1_5_away = sum(p for s, p in spread_dist.items() if s < -1.5)

    total = p_home + p_draw + p_away
    if total > 0:
        spread_0_5_home /= total
        spread_1_5_home /= total
        spread_0_5_away /= total
        spread_1_5_away /= total

    return {
        'home': p_home,
        'draw': p_draw,
        'away': p_away,
        'over_2_5': over_2_5,
        'under_2_5': under_2_5,
        'over_1_5': over_1_5,
        'under_1_5': under_1_5,
        'btts': btts,
        'spread_0_5_home': spread_0_5_home,
        'spread_1_5_home': spread_1_5_home,
        'spread_0_5_away': spread_0_5_away,
        'spread_1_5_away': spread_1_5_away,
        'lambda_home_adj': lam_h,
        'lambda_away_adj': lam_a,
        'remaining_fraction': remaining,
    }


# ─── Lambda sourcing: DC model (primary) or sharp odds (fallback) ────────────

def _get_lambdas_dc(home: str, away: str) -> tuple[float, float] | None:
    """Get pre-match lambdas from our DC model."""
    model = _load_dc_model()
    if model is None:
        return None
    pred = model.predict_or_none(home, away)
    if pred is None:
        return None
    return pred['lambda_home'], pred['lambda_away']


def _get_lambdas_sharp(event: dict) -> tuple[float, float] | None:
    """Derive lambdas from sharp consensus odds (fallback)."""
    hp = event.get('home_prob', 0)
    dp = event.get('draw_prob', 0)
    ap = event.get('away_prob', 0)
    if not (hp > 0 and ap > 0):
        return None
    if dp is None or dp <= 0:
        dp = max(0.01, 1.0 - hp - ap)
    return derive_lambdas(hp, dp, ap)


def _get_team_ids(home: str, away: str) -> tuple[int, int] | None:
    """Look up api-football team IDs by team names (from live fixture lookup)."""
    api_key = os.getenv('FOOTBALL_API_KEY', '')
    if not api_key:
        return None
    try:
        import requests as _req
        resp = _req.get(
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


# ─── Live data fetching ──────────────────────────────────────────────────────

def _fetch_live_scores_api() -> dict[str, dict]:
    """
    Fetch live match data from available APIs.
    Tries api-football.com first (richer data: red cards, shots),
    then falls back to The Odds API /scores endpoint.
    """
    scores = _fetch_scores_football_api()
    if scores:
        return scores
    return _fetch_scores_odds_api()


def _fetch_scores_football_api() -> dict[str, dict]:
    """
    Fetch from api-football.com with enriched data.
    Returns red cards, shots on target alongside score + minute.
    """
    api_key = os.getenv('FOOTBALL_API_KEY', '')
    if not api_key:
        return {}

    try:
        import requests as _req
        resp = _req.get(
            'https://v3.football.api-sports.io/fixtures',
            params={'live': 'all'},
            headers={'x-apisports-key': api_key},
            timeout=8,
        )
        if resp.status_code != 200:
            log.debug(f'[poisson] api-football HTTP {resp.status_code}')
            return {}

        data = resp.json()
        if not isinstance(data, dict) or 'response' not in data:
            return {}

        scores = {}
        for fixture in data['response']:
            home = fixture.get('teams', {}).get('home', {}).get('name', '')
            away = fixture.get('teams', {}).get('away', {}).get('name', '')
            goals = fixture.get('goals', {})
            status = fixture.get('fixture', {}).get('status', {})

            if not home or not away:
                continue

            home_goals = goals.get('home')
            away_goals = goals.get('away')
            elapsed = status.get('elapsed')

            if home_goals is None or away_goals is None or not elapsed:
                continue

            # Extract red cards from events
            home_reds = 0
            away_reds = 0
            events = fixture.get('events', [])
            for ev in events:
                if ev.get('type') == 'Card' and ev.get('detail') == 'Red Card':
                    team_name = ev.get('team', {}).get('name', '')
                    if team_name == home:
                        home_reds += 1
                    elif team_name == away:
                        away_reds += 1

            # Extract shots on target from statistics
            home_shots_on = None
            away_shots_on = None
            stats = fixture.get('statistics', [])
            for team_stat in stats:
                team_name = team_stat.get('team', {}).get('name', '')
                for s in team_stat.get('statistics', []):
                    if s.get('type') == 'Shots on Goal':
                        val = s.get('value')
                        if val is not None:
                            if team_name == home:
                                home_shots_on = int(val)
                            elif team_name == away:
                                away_shots_on = int(val)

            match_data = {
                'home_goals': int(home_goals),
                'away_goals': int(away_goals),
                'minute': int(elapsed),
                'score_known': True,
                'home_reds': home_reds,
                'away_reds': away_reds,
                'home_shots_on': home_shots_on,
                'away_shots_on': away_shots_on,
                'source': 'api-football',
            }
            for key in _match_keys(home, away):
                scores[key] = match_data

        if scores:
            log.info(f'[poisson] Live data via api-football: {len(scores)} match(es)')
        return scores
    except Exception as e:
        log.debug(f'[poisson] api-football error: {e}')
        return {}


def _fetch_scores_odds_api() -> dict[str, dict]:
    """Fetch live scores from The Odds API /scores endpoint."""
    import requests as _req
    api_key = os.getenv('THE_ODDS_API_KEY', '')
    if not api_key:
        return {}

    sports = ['soccer_uefa_champs_league', 'soccer_epl', 'soccer_spain_la_liga',
              'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_france_ligue_one',
              'soccer_conmebol_libertadores', 'soccer_conmebol_copa_sudamericana']

    scores = {}
    now = datetime.now(timezone.utc)
    for sport in sports:
        try:
            resp = _req.get(
                f'https://api.the-odds-api.com/v4/sports/{sport}/scores',
                params={'apiKey': api_key, 'daysFrom': 1},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            for ev in resp.json():
                if ev.get('completed') or not ev.get('scores'):
                    continue
                home = ev.get('home_team', '')
                away = ev.get('away_team', '')
                sc = {s['name']: int(s['score']) for s in ev['scores'] if s.get('score') is not None}
                if home not in sc or away not in sc:
                    continue
                commence = datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
                elapsed = int((now - commence).total_seconds() / 60)
                elapsed = max(1, min(elapsed, 90))
                match_data = {
                    'home_goals': sc[home],
                    'away_goals': sc[away],
                    'minute': elapsed,
                    'score_known': True,
                    'home_reds': 0,
                    'away_reds': 0,
                    'home_shots_on': None,
                    'away_shots_on': None,
                    'source': 'odds-api',
                }
                for key in _match_keys(home, away):
                    scores[key] = match_data
        except Exception as e:
            log.debug(f'[poisson] Odds API scores error ({sport}): {e}')
            continue
    if scores:
        log.info(f'[poisson] Live scores via The Odds API: {len(scores)} match(es)')
    return scores


# ─── PM score extraction (primary source — zero API cost) ────────────────────

def _get_live_scores_from_pm(pm_markets: list[dict]) -> dict[str, dict]:
    """
    Extract live score + minute directly from PM event data.

    The Gamma API provides ``score``, ``elapsed``, ``period``, ``live``,
    and ``ended`` fields on match events.  These are passed through by
    ``fetch_pm_markets_today`` as ``_score``, ``_elapsed``, etc.

    This is the primary live-score source — it requires no external API
    key and covers every market PM has open.
    """
    scores: dict[str, dict] = {}

    for mkt in pm_markets:
        event_title = mkt.get('_event_title', '')
        if not event_title or event_title in scores:
            continue

        if not mkt.get('_live') or mkt.get('_ended'):
            continue

        raw_score = mkt.get('_score')
        elapsed = mkt.get('_elapsed')
        if not raw_score or not elapsed:
            continue
        try:
            elapsed = int(elapsed)
        except (ValueError, TypeError):
            continue

        parts = str(raw_score).split('-')
        if len(parts) != 2:
            continue
        try:
            hg, ag = int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, TypeError):
            continue

        home_pm = mkt.get('_home_team', '')
        away_pm = mkt.get('_away_team', '')

        match_data = {
            'home_goals': hg,
            'away_goals': ag,
            'minute': elapsed,
            'score_known': True,
            'home_reds': 0,
            'away_reds': 0,
            'home_shots_on': None,
            'away_shots_on': None,
            'source': 'pm-live',
            'period': mkt.get('_period', ''),
        }
        for key in _match_keys(home_pm, away_pm):
            scores[key] = match_data

    return scores


# ─── DB: strategy helper ─────────────────────────────────────────────────────

def _get_or_create_poisson_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = 'PM-vs-Poisson In-Play' LIMIT 1")
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
        VALUES ('PM In-Play Poisson Edge Scanner',
                'In-play Polymarket prices diverge from dynamic Poisson-model fair values.',
                'PM in-play markets are thin and slow to update; dynamic Poisson model with DC lambdas, red card adjustment, and trailing-team push provides mathematical fair value.',
                'agent', 'live', 'agent')
        RETURNING id
    """)
    hyp_id = cur.fetchone()[0]
    conn.commit()
    cur.execute("""
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, 'PM-vs-Poisson In-Play', %s::jsonb, NOW())
        RETURNING id
    """, (hyp_id, json.dumps({
        'model': 'dynamic_poisson_v2',
        'lambda_source': 'dc_model_primary_sharp_fallback',
        'adjustments': ['red_card', 'trailing_push_70', 'goal_momentum', 'nonlinear_time'],
        'edge_threshold_pp': INPLAY_EDGE_THRESHOLD_PP,
        'stake': '1u flat', 'phase': 'paper-only',
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    return strat_id


# ─── Outcome classification ──────────────────────────────────────────────────

def _classify_pm_outcome(title: str, home: str, away: str) -> str | None:
    """Classify a PM market question into an outcome key (1x2, totals, spreads, etc)."""
    t = title.lower()

    # Spreads: "X -1.5", "Y +0.5", etc
    # Detect spreads with format like "-1.5" or "+0.5"
    if any(spread in t for spread in ['-1.5', '-0.5', '+0.5', '+1.5', 'spread']):
        if '-1.5' in t:
            if _norm(home)[:6] in _norm(t):
                return 'spread_h_minus_1_5'
            if _norm(away)[:6] in _norm(t):
                return 'spread_a_minus_1_5'
        if '-0.5' in t:
            if _norm(home)[:6] in _norm(t):
                return 'spread_h_minus_0_5'
            if _norm(away)[:6] in _norm(t):
                return 'spread_a_minus_0_5'
        if '+0.5' in t or ('+' in t and '0.5' in t):
            if _norm(home)[:6] in _norm(t):
                return 'spread_h_plus_0_5'
            if _norm(away)[:6] in _norm(t):
                return 'spread_a_plus_0_5'
        if '+1.5' in t or ('+' in t and '1.5' in t):
            if _norm(home)[:6] in _norm(t):
                return 'spread_h_plus_1_5'
            if _norm(away)[:6] in _norm(t):
                return 'spread_a_plus_1_5'

    # Totals
    if 'draw' in t:
        return 'draw'
    if 'over' in t and '2.5' in t:
        return 'over_2_5'
    if 'under' in t and '2.5' in t:
        return 'under_2_5'
    if 'over' in t and '1.5' in t:
        return 'over_1_5'
    if 'under' in t and '1.5' in t:
        return 'under_1_5'
    if 'both teams' in t or 'btts' in t:
        return 'btts'

    # Home/away win
    if _norm(home)[:6] in _norm(t):
        return 'home'
    if _norm(away)[:6] in _norm(t):
        return 'away'

    # Check for "win" patterns
    if 'win' in t:
        home_n = _norm(home)
        away_n = _norm(away)
        t_n = _norm(t)
        if home_n[:5] in t_n:
            return 'home'
        if away_n[:5] in t_n:
            return 'away'

    return None


# ─── Main scan ───────────────────────────────────────────────────────────────

def run(dry_run: bool = False, tracker: Any = None) -> list[dict]:
    """
    Scan for in-play Poisson edges on Polymarket.
    Uses DC model lambdas (primary) or sharp odds (fallback).
    Validates with live bookmaker consensus (Pinnacle/Betfair/Smarkets).
    Optionally accepts a LiveMatchTracker for pressure-based lambda adjustments.
    Returns list of trade dicts.
    """
    log.info('\n' + '=' * 60)
    log.info('[poisson v2] Dynamic in-play scan — %s',
             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    log.info('=' * 60)

    # 1. Try to load DC model
    dc = _load_dc_model()

    # 1b. Initialize live odds tracker (for Pinnacle/Betfair consensus)
    odds_tracker = LiveOddsTracker()

    # 1c. Initialize injury tracker
    injury_tracker = InjuryTracker()

    # 1d. Initialize market flow tracker (for smart money signals)
    market_flow = MarketFlow()

    # 2. Get cached sharp odds (fallback)
    sharp_lookup = paper_trader.get_cached_sharp_odds()
    if not dc and not sharp_lookup:
        log.info('[poisson] No DC model and no cached sharp odds — cannot price')
        return []

    # 3. Fetch current PM markets (includes live score data from Gamma API)
    pm_markets = fetch_pm_markets_today()
    if not pm_markets:
        log.info('[poisson] No PM markets available')
        return []

    # 4. Build live scores — PM event data is the primary source (zero API cost,
    #    covers every match PM has open).  api-football enriches with red cards
    #    and shots when available.
    live_scores = _get_live_scores_from_pm(pm_markets)

    # Enrich with api-football data (red cards, shots on target)
    api_scores = _fetch_live_scores_api()
    for key, api_data in api_scores.items():
        if key in live_scores:
            live_scores[key]['home_reds'] = api_data.get('home_reds', 0)
            live_scores[key]['away_reds'] = api_data.get('away_reds', 0)
            live_scores[key]['home_shots_on'] = api_data.get('home_shots_on')
            live_scores[key]['away_shots_on'] = api_data.get('away_shots_on')
            if api_data.get('source') == 'api-football':
                live_scores[key]['minute'] = api_data['minute']
        else:
            live_scores[key] = api_data

    if not live_scores:
        log.info('[poisson] No live matches found — skipping in-play scan')
        return []

    log.info(f'[poisson] {len(live_scores)} live match(es) found')
    for key, sd in live_scores.items():
        reds_info = ''
        if sd.get('home_reds') or sd.get('away_reds'):
            reds_info = f'  red H={sd["home_reds"]} A={sd["away_reds"]}'
        src = sd.get('source', '?')
        log.info(f'  {key}: {sd["home_goals"]}-{sd["away_goals"]} ({sd["minute"]}\') [{src}]{reds_info}')

    # 4b. Poll live tracker for pressure signals (if available)
    pressure_signals = {}
    edge_signals_by_match = {}
    if tracker:
        try:
            pressure_signals = tracker.poll()
            from .live_tracker import derive_edge_signals
            for fid, sig in pressure_signals.items():
                edges = derive_edge_signals(sig)
                if edges:
                    match_key = f'{_norm(sig.home)}_{_norm(sig.away)}'
                    edge_signals_by_match[match_key] = edges
                    log.info(f'  {sig.home} vs {sig.away}: danger H={sig.home_danger_index:.0f} '
                             f'A={sig.away_danger_index:.0f} | {len(edges)} signal(s)')
                    for es in edges:
                        log.info(f'     [{es.signal_type}] {es.direction} conf={es.confidence:.0%}')
        except Exception as e:
            log.warning(f'[poisson] Tracker error (non-fatal): {e}')

    # 5. Match PM markets to live scores and price
    conn = _conn() if not dry_run else None
    trades_found: list[dict] = []

    try:
        strategy_id = None
        if conn:
            strategy_id = _get_or_create_poisson_strategy(conn)

        seen_matches = set()

        for mkt in pm_markets:
            home_pm = mkt.get('_home_team')
            away_pm = mkt.get('_away_team')
            if not home_pm or not away_pm:
                continue

            # Find live score
            score_data = None
            for key in _match_keys(home_pm, away_pm):
                if key in live_scores:
                    score_data = live_scores[key]
                    break

            if not score_data or not score_data.get('score_known'):
                continue

            home_goals = score_data['home_goals']
            away_goals = score_data['away_goals']
            minute = score_data['minute']
            home_reds = score_data.get('home_reds', 0)
            away_reds = score_data.get('away_reds', 0)

            # Get lambdas: DC model first, sharp odds fallback
            lam_h, lam_a = None, None
            lambda_source = ''
            injury_adjustments = None

            # Try DC model
            dc_lambdas = _get_lambdas_dc(home_pm, away_pm)
            if dc_lambdas:
                lam_h, lam_a = dc_lambdas
                lambda_source = 'dc_model'

            # Fallback: sharp odds
            if lam_h is None:
                event = _fuzzy_find_event_two(home_pm, away_pm, sharp_lookup) if sharp_lookup else None
                if event:
                    sharp_lambdas = _get_lambdas_sharp(event)
                    if sharp_lambdas:
                        lam_h, lam_a = sharp_lambdas
                        lambda_source = 'sharp_consensus'

            if lam_h is None:
                continue

            # Apply injury adjustments
            try:
                team_ids = _get_team_ids(home_pm, away_pm)
                if team_ids:
                    home_tid, away_tid = team_ids
                    inj_adj = injury_tracker.get_fixture_adjustments(0, home_tid, away_tid)
                    if inj_adj and (inj_adj['home'] < 1.0 or inj_adj['away'] < 1.0):
                        lam_h *= inj_adj['home']
                        lam_a *= inj_adj['away']
                        injury_adjustments = inj_adj
            except Exception as e:
                log.debug(f'[poisson] Injury lookup error (non-fatal): {e}')

            home = home_pm
            away = away_pm

            # Apply pressure-based lambda adjustments from live tracker
            match_key = f'{_norm(home)}_{_norm(away)}'
            pressure_adjustments = []
            if match_key in edge_signals_by_match:
                for es in edge_signals_by_match[match_key]:
                    if es.confidence >= 0.5:
                        lam_h *= es.lambda_boost_home
                        lam_a *= es.lambda_boost_away
                        pressure_adjustments.append(
                            f'{es.signal_type}({es.direction}, conf={es.confidence:.0%})')

            if match_key not in seen_matches:
                seen_matches.add(match_key)
                adj_info = []
                if home_reds or away_reds:
                    adj_info.append(f'reds: H={home_reds} A={away_reds}')
                if injury_adjustments and (injury_adjustments['home'] < 1.0 or injury_adjustments['away'] < 1.0):
                    inj_str = f'injuries: H={injury_adjustments["home"]:.2%} A={injury_adjustments["away"]:.2%}'
                    adj_info.append(inj_str)
                if minute >= TRAILING_PUSH_MINUTE and home_goals != away_goals:
                    adj_info.append('trailing push active')
                if pressure_adjustments:
                    adj_info.extend(pressure_adjustments)
                adj_str = f'  [{", ".join(adj_info)}]' if adj_info else ''
                log.info(f'\n  ⚽ {home} vs {away} [{home_goals}-{away_goals} {minute}\'] '
                         f'λ={lam_h:.2f}/{lam_a:.2f} ({lambda_source}){adj_str}')

            # Calculate dynamic in-play probs
            fair = inplay_probs(lam_h, lam_a, home_goals, away_goals, minute,
                                home_reds=home_reds, away_reds=away_reds)

            # Classify PM market outcome
            title = mkt.get('question', '')
            yes_price = mkt['_yes_price']

            outcome_key = _classify_pm_outcome(title, home, away)
            if not outcome_key:
                continue

            # Map spread outcomes to fair probabilities
            fair_prob = fair.get(outcome_key, 0)

            # If outcome not in fair dict, check for spread mappings
            if fair_prob <= 0:
                if outcome_key == 'spread_h_minus_0_5':
                    fair_prob = fair.get('spread_0_5_home', 0)
                elif outcome_key == 'spread_h_minus_1_5':
                    fair_prob = fair.get('spread_1_5_home', 0)
                elif outcome_key == 'spread_a_minus_0_5':
                    fair_prob = fair.get('spread_0_5_away', 0)
                elif outcome_key == 'spread_a_minus_1_5':
                    fair_prob = fair.get('spread_1_5_away', 0)
                elif outcome_key == 'spread_h_plus_0_5':
                    # Home +0.5 = Home doesn't lose by more than 0 = 1 - (spread_0_5_away)
                    fair_prob = 1.0 - fair.get('spread_0_5_away', 0)
                elif outcome_key == 'spread_h_plus_1_5':
                    # Home +1.5 = Home doesn't lose by more than 1 = 1 - (spread_1_5_away)
                    fair_prob = 1.0 - fair.get('spread_1_5_away', 0)
                elif outcome_key == 'spread_a_plus_0_5':
                    # Away +0.5 = Away doesn't lose by more than 0 = 1 - (spread_0_5_home)
                    fair_prob = 1.0 - fair.get('spread_0_5_home', 0)
                elif outcome_key == 'spread_a_plus_1_5':
                    # Away +1.5 = Away doesn't lose by more than 1 = 1 - (spread_1_5_home)
                    fair_prob = 1.0 - fair.get('spread_1_5_home', 0)

            if fair_prob <= 0 or fair_prob >= 1.0:
                continue

            edge_pp = (fair_prob - yes_price) * 100

            pm_odds = round(1 / yes_price, 3)
            fair_odds = round(1 / fair_prob, 3)
            score_str = f'{home_goals}-{away_goals}'

            # Fetch live bookmaker consensus (Pinnacle/Betfair/Smarkets)
            consensus_info = ''
            consensus_validation = ''
            line_movement_info = ''
            try:
                # Find fixture_id from home/away names
                fixture_id = odds_tracker.get_fixture_id(home, away)
                if fixture_id:
                    # Determine bet type from outcome
                    bet_type_map = {
                        'home': 'Match Winner', 'draw': 'Match Winner', 'away': 'Match Winner',
                        'over_2_5': 'Over 2.5', 'under_2_5': 'Over 2.5',
                        'over_1_5': 'Over 1.5', 'under_1_5': 'Over 1.5',
                        'btts': 'BTTS',
                    }
                    bet_type = bet_type_map.get(outcome_key, 'Match Winner')

                    # Get current consensus
                    consensus = odds_tracker.get_consensus(
                        fixture_id, home, away, bet_type=bet_type
                    )
                    if consensus:
                        edge_vs_bm = get_pm_vs_bookmaker_edge(yes_price, consensus, outcome_key)
                        if edge_vs_bm:
                            bm_used = edge_vs_bm.get('bookmakers_used', [])
                            bm_edge = edge_vs_bm.get('edge_pp', 0)
                            conf = edge_vs_bm.get('confidence', '?')
                            consensus_info = f' | Bookmakers={bm_used} {bm_edge:+.1f}pp ✓'
                            # Consensus validation: if both model and bookmakers agree, confidence ↑
                            if abs(bm_edge) >= 2.0 and abs(edge_pp) >= 2.0 and (bm_edge * edge_pp > 0):
                                consensus_validation = ' [STRONG]'

                    # Detect line movement from kickoff
                    line_moves = odds_tracker.get_line_movement(fixture_id, home, away, bet_type=bet_type)
                    if line_moves:
                        moves_str = ' | '.join([f'{k}={v:+.1f}pp' for k, v in line_moves.items()])
                        line_movement_info = f' | LineMove: {moves_str}'
                        # Line movement is a smart money signal — boost confidence if in our direction
                        for key, move_pp in line_moves.items():
                            if outcome_key in key and move_pp * edge_pp > 0:  # same direction
                                consensus_validation = ' [VERY STRONG]'

            except Exception as e:
                log.debug(f'[poisson] Consensus/LineMove error: {e}')

            # Check for smart money signals (market flow)
            market_flow_info = ''
            smart_money_validation = ''
            try:
                if mkt.get('id'):
                    market_id = mkt.get('id')
                    # Map outcome to direction for market flow check
                    flow_direction = {
                        'home': 'yes', 'draw': 'yes', 'away': 'no',
                        'over_2_5': 'yes', 'over_1_5': 'yes',
                        'under_2_5': 'no', 'under_1_5': 'no',
                        'btts': 'yes'
                    }.get(outcome_key, 'neutral')

                    if edge_pp > 0:
                        flow_direction = 'yes'
                    elif edge_pp < 0:
                        flow_direction = 'no'

                    flow_signal = market_flow.detect_smart_money_signal(market_id, flow_direction)
                    if flow_signal and flow_signal.whale_volume >= WHALE_VOLUME_USDC:
                        market_flow_info = f' | Whale: {flow_signal.whale_side} {flow_signal.whale_volume/1000:.0f}k'
                        if flow_signal.confidence >= 0.7 and flow_signal.whale_side == flow_direction:
                            smart_money_validation = ' [WHALE ✓]'
                        elif flow_signal.whale_side != flow_direction and flow_direction != 'neutral':
                            smart_money_validation = ' [whale opposes]'
            except Exception as e:
                log.debug(f'[poisson] MarketFlow error (non-fatal): {e}')

            sign = ('✅' if edge_pp >= INPLAY_EDGE_THRESHOLD_PP else
                    '~' if edge_pp > 1 else '·')
            log.info(f'  {sign} {outcome_key.upper()}: PM={yes_price:.3f} ({pm_odds}x) | '
                     f'Model={fair_prob:.3f} ({fair_odds}x) | Edge={edge_pp:+.1f}pp{consensus_info}{market_flow_info}{line_movement_info}{consensus_validation}{smart_money_validation}')

            if edge_pp < INPLAY_EDGE_THRESHOLD_PP:
                continue

            # Build reasoning
            adjustments_applied = []
            if home_reds or away_reds:
                adjustments_applied.append(f'red cards (H={home_reds} A={away_reds})')
            if minute >= TRAILING_PUSH_MINUTE and home_goals != away_goals:
                adjustments_applied.append(f'trailing-team push ({minute}\')')
            if home_goals != away_goals:
                adjustments_applied.append('goal momentum')
            if pressure_adjustments:
                adjustments_applied.extend(pressure_adjustments)

            outcome_label = {
                'home': f'{home} win', 'draw': 'Draw', 'away': f'{away} win',
                'over_2_5': 'Over 2.5', 'under_2_5': 'Under 2.5',
                'over_1_5': 'Over 1.5', 'under_1_5': 'Under 1.5',
                'btts': 'BTTS',
            }.get(outcome_key, outcome_key)

            sources = {
                'model': 'dynamic_poisson_v2',
                'lambda_source': lambda_source,
                'score': score_str,
                'minute': minute,
                'lambda_home_pre': round(lam_h, 4),
                'lambda_away_pre': round(lam_a, 4),
                'lambda_home_adj': round(fair['lambda_home_adj'], 4),
                'lambda_away_adj': round(fair['lambda_away_adj'], 4),
                'home_reds': home_reds,
                'away_reds': away_reds,
                'adjustments': adjustments_applied,
                'pressure_signals': pressure_adjustments if pressure_adjustments else None,
            }

            reasoning = (
                f'In-play dynamic Poisson edge (v2).\n'
                f'Match: {home} vs {away}  [{score_str} at {minute}\']\n'
                f'Selection: {outcome_label}\n\n'
                f'PM price:       {yes_price:.4f}  (odds {pm_odds})\n'
                f'Model fair:     {fair_prob:.4f}  (odds {fair_odds})\n'
                f'Edge:           +{edge_pp:.2f}pp\n\n'
                f'Lambda source:  {lambda_source}\n'
                f'Pre-match λ:    home={lam_h:.3f}  away={lam_a:.3f}\n'
                f'Adjusted λ:     home={fair["lambda_home_adj"]:.3f}  away={fair["lambda_away_adj"]:.3f}\n'
                f'Remaining:      {max(0, 90 - minute)} min ({fair["remaining_fraction"]:.1%})\n'
            )
            if adjustments_applied:
                reasoning += f'Adjustments:    {", ".join(adjustments_applied)}\n'
            # Add pressure signal details
            if match_key in edge_signals_by_match:
                reasoning += '\nPressure signals:\n'
                for es in edge_signals_by_match[match_key]:
                    reasoning += f'  • [{es.signal_type}] {es.reasoning}\n'

            trade_info = {
                'match': f'{home} vs {away}',
                'score': score_str,
                'minute': minute,
                'outcome': outcome_label,
                'pm_price': yes_price,
                'pm_odds': pm_odds,
                'fair_prob': round(fair_prob, 4),
                'fair_odds': fair_odds,
                'edge_pp': round(edge_pp, 2),
                'lambda_source': lambda_source,
                'adjustments': adjustments_applied,
            }
            trades_found.append(trade_info)

            if not dry_run and conn:
                from .paper_trader import _upsert_pm_market, _write_paper_trade
                from .tools.db import find_match_id as _fmi
                market_db_id = _upsert_pm_market(conn, mkt)
                db_match_id = _fmi(conn, home, away)
                trade_id = _write_paper_trade(
                    conn, strategy_id, market_db_id,
                    outcome_label, yes_price, fair_prob,
                    sources, edge_pp, reasoning,
                    match_id=db_match_id,
                )
                if trade_id:
                    log.info(f'     → Paper trade #{trade_id} logged ✅')
                    trade_info['trade_id'] = trade_id
                else:
                    log.info(f'     → Skipped (duplicate within 30 min)')

    finally:
        if conn:
            conn.close()

    log.info(f'\n[poisson v2] Done — {len(trades_found)} in-play edge(s) found')
    return trades_found


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Poisson In-Play Trader v2')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-tracker', action='store_true', help='Disable live pressure tracker')
    args = parser.parse_args()

    # Need to run consensus first to populate sharp odds cache
    log.info('[poisson] Running consensus scan first to populate sharp odds cache...')
    paper_trader.run(dry_run=True)

    # Initialize live tracker for pressure signals
    live_tracker = None
    if not args.no_tracker:
        try:
            from .live_tracker import LiveMatchTracker
            live_tracker = LiveMatchTracker()
            log.info('[poisson] Live pressure tracker enabled')
        except Exception as e:
            log.warning(f'[poisson] Could not init tracker: {e}')

    trades = run(dry_run=args.dry_run, tracker=live_tracker)
    if trades:
        print(f'\n{"─" * 60}')
        print(f'IN-PLAY EDGES ({len(trades)}):')
        for t in trades:
            adj = f'  [{", ".join(t["adjustments"])}]' if t.get('adjustments') else ''
            print(f"  {t['match']} [{t['score']} {t['minute']}'] | {t['outcome']} | "
                  f"PM={t['pm_price']:.3f} | Model={t['fair_prob']:.4f} | "
                  f"Edge={t['edge_pp']:+.2f}pp | λ={t['lambda_source']}{adj}")
    else:
        print('\nNo in-play edges found.')
