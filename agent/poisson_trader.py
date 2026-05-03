"""
Poisson In-Play Trader — PM-vs-Poisson strategy.

Derives pre-match Poisson lambdas from sharp consensus odds (cached by
paper_trader), then calculates fair in-play probabilities given current
score + minute. Compares against live Polymarket prices.

Zero Odds API calls — reuses the morning sharp odds cache.
Only polls the free Polymarket Gamma API for live prices.

Usage:
    from agent import poisson_trader
    trades = poisson_trader.run()               # scan live matches
    trades = poisson_trader.run(dry_run=True)    # print only
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize

from . import paper_trader
from .paper_trader import (
    fetch_pm_markets_today, _norm, _match_keys,
    _fuzzy_find_event_two, _extract_home_away_from_event_title,
    _conn, _serial, EDGE_THRESHOLD_PP, STAKE_UNITS, GAMMA_API, _get,
)
from .tools.db import log_agent_run

log = logging.getLogger(__name__)

INPLAY_EDGE_THRESHOLD_PP = 5.0  # slightly higher threshold for in-play
MAX_GOALS = 8


# ─── Poisson math ────────────────────────────────────────────────────────────

def _poisson_match_probs(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    """
    Calculate P(home win), P(draw), P(away win) from independent Poisson.
    """
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    for i in range(MAX_GOALS + 1):
        p_i = poisson.pmf(i, lam_h)
        for j in range(MAX_GOALS + 1):
            p_j = poisson.pmf(j, lam_a)
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


def inplay_probs(lam_h: float, lam_a: float,
                 home_goals: int, away_goals: int,
                 minute: int) -> dict[str, float]:
    """
    Given pre-match lambdas and current score at `minute`, calculate
    fair probabilities for the final result using remaining-time Poisson.
    """
    remaining = max(0, (90 - minute)) / 90.0
    lam_h_rem = lam_h * remaining
    lam_a_rem = lam_a * remaining

    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0

    for k in range(MAX_GOALS + 1):
        pk = poisson.pmf(k, lam_h_rem) if lam_h_rem > 0 else (1.0 if k == 0 else 0.0)
        for j in range(MAX_GOALS + 1):
            pj = poisson.pmf(j, lam_a_rem) if lam_a_rem > 0 else (1.0 if j == 0 else 0.0)
            p_kj = pk * pj
            final_h = home_goals + k
            final_a = away_goals + j
            if final_h > final_a:
                p_home += p_kj
            elif final_h == final_a:
                p_draw += p_kj
            else:
                p_away += p_kj

    total = p_home + p_draw + p_away
    if total > 0:
        p_home /= total
        p_draw /= total
        p_away /= total

    return {'home': p_home, 'draw': p_draw, 'away': p_away}


# ─── Live score fetching ─────────────────────────────────────────────────────

def _get_live_scores_from_pm(pm_markets: list[dict]) -> dict[str, dict]:
    """
    Try to extract live score + minute from PM market metadata.
    Returns {event_title: {home_goals, away_goals, minute}} for in-play matches.

    PM markets don't always expose live scores directly, so this is best-effort.
    Falls back to checking if a match is likely in-play based on commence_time.
    """
    scores = {}
    now = datetime.now(timezone.utc)

    for mkt in pm_markets:
        event_title = mkt.get('_event_title', '')
        if not event_title or event_title in scores:
            continue

        commence = mkt.get('_commence_time')
        if commence and isinstance(commence, datetime):
            elapsed = (now - commence).total_seconds() / 60
            if 0 <= elapsed <= 105:
                # Match is likely in-play but we don't know the score
                # Store as "in-play but score unknown"
                scores[event_title] = {
                    'home_goals': None,
                    'away_goals': None,
                    'minute': int(min(elapsed, 90)),
                    'score_known': False,
                }

    return scores


def _fetch_live_scores_api() -> dict[str, dict]:
    """
    Fetch live scores from a free API. Returns {normalized_key: {home_goals, away_goals, minute}}.

    Uses api-football.com free tier or similar. Returns empty dict if unavailable.
    """
    api_key = os.getenv('FOOTBALL_API_KEY', '')
    if not api_key:
        return {}

    try:
        data = _get('https://v3.football.api-sports.io/fixtures', params={
            'live': 'all',
        }, timeout=8)
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

            if home_goals is not None and away_goals is not None and elapsed:
                for key in _match_keys(home, away):
                    scores[key] = {
                        'home_goals': int(home_goals),
                        'away_goals': int(away_goals),
                        'minute': int(elapsed),
                        'score_known': True,
                    }
        return scores
    except Exception as e:
        log.debug(f'[poisson] Live scores API error: {e}')
        return {}


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
                'In-play Polymarket prices diverge from Poisson-model fair values derived from sharp pre-match odds.',
                'PM in-play markets are thin and slow to update; Poisson model provides a mathematical fair value anchor.',
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
        'model': 'independent_poisson',
        'edge_threshold_pp': INPLAY_EDGE_THRESHOLD_PP,
        'lambda_source': 'sharp_consensus_pre_match',
        'stake': '1u flat', 'phase': 'paper-only',
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    return strat_id


# ─── Main scan ───────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> list[dict]:
    """
    Scan for in-play Poisson edges on Polymarket.
    Returns list of trade dicts.
    """
    log.info('\n' + '=' * 60)
    log.info('[poisson] In-play Poisson scan — %s',
             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    log.info('=' * 60)

    # 1. Get cached sharp odds (from morning consensus scan)
    sharp_lookup = paper_trader.get_cached_sharp_odds()
    if not sharp_lookup:
        log.info('[poisson] No cached sharp odds — run consensus scan first')
        return []

    # 2. Get live scores
    live_scores = _fetch_live_scores_api()
    if not live_scores:
        log.info('[poisson] No live scores available — skipping in-play scan')
        log.info('[poisson] Set FOOTBALL_API_KEY in ingest/.env for live score data')
        return []

    # 3. Fetch current PM markets
    pm_markets = fetch_pm_markets_today()
    if not pm_markets:
        log.info('[poisson] No PM markets available')
        return []

    # 4. Match PM markets to sharp events + live scores
    conn = _conn() if not dry_run else None
    trades_found: list[dict] = []

    try:
        strategy_id = None
        if conn:
            strategy_id = _get_or_create_poisson_strategy(conn)

        for mkt in pm_markets:
            home_pm = mkt.get('_home_team')
            away_pm = mkt.get('_away_team')
            if not home_pm or not away_pm:
                continue

            # Find sharp event
            event = _fuzzy_find_event_two(home_pm, away_pm, sharp_lookup)
            if not event:
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
            home = event['home']
            away = event['away']

            # 5. Derive Poisson lambdas from sharp odds
            hp = event.get('home_prob', 0)
            dp = event.get('draw_prob', 0)
            ap = event.get('away_prob', 0)
            if not (hp > 0 and ap > 0):
                continue

            if dp is None or dp <= 0:
                dp = max(0.01, 1.0 - hp - ap)

            lam_h, lam_a = derive_lambdas(hp, dp, ap)

            # 6. Calculate in-play fair probs
            fair = inplay_probs(lam_h, lam_a, home_goals, away_goals, minute)

            # 7. Compare each outcome against PM price
            title = mkt.get('question', '')
            yes_price = mkt['_yes_price']

            # Determine which outcome this PM market is about
            t_lower = title.lower()
            if 'draw' in t_lower:
                outcome_key = 'draw'
            elif _norm(home_pm)[:6] in _norm(t_lower):
                outcome_key = 'home'
            elif _norm(away_pm)[:6] in _norm(t_lower):
                outcome_key = 'away'
            else:
                continue

            fair_prob = fair.get(outcome_key, 0)
            if fair_prob <= 0:
                continue

            edge_pp = (fair_prob - yes_price) * 100

            score_str = f'{home_goals}-{away_goals}'
            pm_odds = round(1 / yes_price, 3)
            fair_odds = round(1 / fair_prob, 3)

            sign = ('✅' if edge_pp >= INPLAY_EDGE_THRESHOLD_PP else
                    '~' if edge_pp > 1 else '·')
            log.info(f'  {sign} {home} vs {away}  [{score_str} {minute}\']')
            log.info(f'     {outcome_key.upper()}: PM={yes_price:.3f} ({pm_odds}x) | '
                     f'Poisson={fair_prob:.3f} ({fair_odds}x) | Edge={edge_pp:+.2f}pp')

            if edge_pp < INPLAY_EDGE_THRESHOLD_PP:
                continue

            # 8. Log trade
            sources = {
                'model': 'poisson',
                'score': score_str,
                'minute': minute,
                'lambda_home': round(lam_h, 4),
                'lambda_away': round(lam_a, 4),
                'pre_match_probs': {'home': round(hp, 4), 'draw': round(dp, 4), 'away': round(ap, 4)},
            }

            outcome_label = {
                'home': f'{home} win',
                'draw': 'Draw',
                'away': f'{away} win',
            }[outcome_key]

            reasoning = (
                f'In-play Poisson edge.\n'
                f'Match: {home} vs {away}  [{score_str} at {minute}\']\n'
                f'Selection: {outcome_label}\n\n'
                f'PM price:       {yes_price:.4f}  (odds {pm_odds})\n'
                f'Poisson fair:   {fair_prob:.4f}  (odds {fair_odds})\n'
                f'Edge:           +{edge_pp:.2f}pp\n\n'
                f'Pre-match λ:    home={lam_h:.3f}  away={lam_a:.3f}\n'
                f'Remaining:      {max(0, 90 - minute)} min\n'
            )

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
            }
            trades_found.append(trade_info)

            if not dry_run and conn:
                from .paper_trader import _upsert_pm_market, _write_paper_trade
                market_db_id = _upsert_pm_market(conn, mkt)
                trade_id = _write_paper_trade(
                    conn, strategy_id, market_db_id,
                    outcome_label, yes_price, fair_prob,
                    sources, edge_pp, reasoning,
                )
                if trade_id:
                    log.info(f'     → Paper trade #{trade_id} logged ✅')
                    trade_info['trade_id'] = trade_id
                else:
                    log.info(f'     → Skipped (duplicate within 30 min)')

    finally:
        if conn:
            conn.close()

    log.info(f'\n[poisson] Done — {len(trades_found)} in-play edge(s) found')
    return trades_found


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Poisson In-Play Trader')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    # Need to run consensus first to populate sharp odds cache
    log.info('[poisson] Running consensus scan first to populate sharp odds cache...')
    paper_trader.run(dry_run=True)

    trades = run(dry_run=args.dry_run)
    if trades:
        print(f'\n{"─" * 60}')
        print(f'IN-PLAY EDGES ({len(trades)}):')
        for t in trades:
            print(f"  {t['match']} [{t['score']} {t['minute']}'] | {t['outcome']} | "
                  f"PM={t['pm_price']:.3f} | Poisson={t['fair_prob']:.4f} | "
                  f"Edge={t['edge_pp']:+.2f}pp")
    else:
        print('\nNo in-play edges found.')
