"""
Paper Trader — pre-match edge detector: Polymarket vs fair-value benchmark (today only).

Flow:
  1. Fetch today's football markets from Polymarket Gamma API
  2. Determine fair-value source (tried in order of reliability):
       a. The Odds API — live Pinnacle + Betfair sharp consensus  (EDGE_THRESHOLD_PP = 4pp)
       b. DB Pinnacle closing odds — for fixtures already loaded in match_odds
       c. Model Pricer  — Poisson form model from historical DB + ClubElo fallback
                          (MODEL_EDGE_THRESHOLD_PP = 7pp — higher bar for model edges)
  3. For each PM market, fuzzy-match to a fair-value event
  4. Edge = fair_prob − pm_price (in percentage points)
  5. Log paper_trade for edges > threshold (depends on source)

DB is used to: store trades, load team aliases, check active strategies.

Requirements:
  THE_ODDS_API_KEY in ingest/.env  (optional — free at the-odds-api.com)
  DATABASE_URL in ingest/.env       (required)

Usage:
    python -m agent.paper_trader --dry-run      # scan + print, no DB writes
    python -m agent.paper_trader                # scan + log to DB

    from agent import paper_trader
    trades = paper_trader.run()
    trades = paper_trader.run(dry_run=True)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

from .tools.db import find_match_id

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ─── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL    = os.getenv('DATABASE_URL')
GAMMA_API       = os.getenv('POLYMARKET_GAMMA_API', 'https://gamma-api.polymarket.com').rstrip('/')
ODDS_API_KEY    = os.getenv('THE_ODDS_API_KEY', '')
ODDS_API_BASE   = 'https://api.the-odds-api.com/v4'

EDGE_THRESHOLD_PP       = 4.0   # minimum edge (pp) for sharp-consensus edges (Odds API / DB Pinnacle)
MODEL_EDGE_THRESHOLD_PP = 7.0   # higher bar for model-priced edges (less reliable than sharp)
STAKE_UNITS             = 1.0
DAYS_AHEAD              = 0     # 0 = today only; use --days 1 to include tomorrow too

# Active fair-value source for the current scan (set at runtime)
_FAIR_VALUE_SOURCE: str = 'unknown'   # 'af_pinnacle' | 'odds_api' | 'db_pinnacle' | 'model'

# Sharp books and their weights for consensus
SHARP_BOOKS = {'pinnacle': 0.65, 'betfair_ex_eu': 0.35}

# Football sport keys on The Odds API
TARGET_SPORTS = [
    # Top 5 European leagues
    'soccer_epl',
    'soccer_spain_la_liga',
    'soccer_germany_bundesliga',
    'soccer_italy_serie_a',
    'soccer_france_ligue_one',
    # European cups
    'soccer_uefa_champs_league',
    'soccer_uefa_europa_league',
    'soccer_uefa_europa_conference_league',
    # Second divisions
    'soccer_england_championship',
    'soccer_spain_segunda_division',
    'soccer_germany_bundesliga2',
    'soccer_italy_serie_b',
    'soccer_france_ligue_deux',
    # Other European
    'soccer_netherlands_eredivisie',
    'soccer_portugal_primeira_liga',
    'soccer_turkey_super_league',
    'soccer_spl',                          # Scotland
    'soccer_belgium_first_div',
    'soccer_austria_bundesliga',
    'soccer_netherlands_eredivisie',
    'soccer_denmark_superliga',
    'soccer_norway_eliteserien',
    'soccer_sweden_allsvenskan',
    'soccer_switzerland_superleague',
    'soccer_poland_ekstraklasa',
    'soccer_greece_super_league',
    # South America
    'soccer_conmebol_copa_libertadores',
    'soccer_conmebol_copa_sudamericana',
    'soccer_argentina_primera_division',
    'soccer_brazil_campeonato',
    # Asia
    'soccer_japan_j_league',               # J1 — J2 not on Pinnacle
    'soccer_china_superleague',
    'soccer_korea_kleague1',
    'soccer_saudi_arabia_pro_league',
    # Americas
    'soccer_usa_mls',
    'soccer_mexico_ligamx',
]

# Polymarket competition tag → Odds API sport key
# Derived from PM event tags observed in production
PM_TAG_TO_SPORT: dict[str, str] = {
    'UCL':                          'soccer_uefa_champs_league',
    'UEL':                          'soccer_uefa_europa_league',
    'UECL':                         'soccer_uefa_europa_conference_league',
    'Europa Conference League':     'soccer_uefa_europa_conference_league',
    'Europa League':                'soccer_uefa_europa_league',
    'bundesliga':                   'soccer_germany_bundesliga',
    'Bundesliga 2':                 'soccer_germany_bundesliga2',
    'La Liga':                      'soccer_spain_la_liga',
    'La Liga 2':                    'soccer_spain_segunda_division',
    'Ligue 1':                      'soccer_france_ligue_one',
    'Serie B':                      'soccer_italy_serie_b',
    'EFL Championship':             'soccer_efl_champ',
    'Copa Libertadores':            'soccer_conmebol_copa_libertadores',
    'Copa Sudamericana':            'soccer_conmebol_copa_sudamericana',
    'Japan J League':               'soccer_japan_j_league',
    'Chinese Super League':         'soccer_china_superleague',
    'Saudi Professional League':    'soccer_saudi_arabia_pro_league',
    'MLS':                          'soccer_usa_mls',
    'Denmark Superliga':            'soccer_denmark_superliga',
    'Norway Eliteserien':           'soccer_norway_eliteserien',
    'Turkey Super League':          'soccer_turkey_super_league',
    'Scottish Premiership':         'soccer_spl',
    'Belgium First Division':       'soccer_belgium_first_div',
    'K League 1':                   'soccer_korea_kleague1',
    # Tags with no Pinnacle coverage — silently ignored
    # 'Japan J2 League': None   (Pinnacle doesn't offer J2)
    # 'Ukraine Premier Liha': None
    # 'Liga Nacional Guatemala': None
    # 'CONCACAF Champions Cup': None
}

# PM football detection keywords
FOOTBALL_KEYWORDS = [
    'soccer', 'football', 'premier league', 'la liga', 'bundesliga', 'serie a',
    'ligue 1', 'champions league', 'europa league', 'epl', 'world cup',
    'liverpool', 'arsenal', 'chelsea', 'man city', 'man utd', 'manchester',
    'tottenham', 'real madrid', 'barcelona', 'atletico', 'atlético', 'bayern',
    'dortmund', 'inter milan', 'ac milan', 'juventus', 'napoli', 'psg',
    'newcastle', 'aston villa', 'porto', 'benfica', 'ajax', 'psv',
    'celtic', 'rangers', 'marseille', 'lyon', 'monaco', 'sevilla',
    'borussia', 'bayer', 'rb leipzig', 'real madrid', 'sporting',
]
NON_FOOTBALL = [
    'tweet', 'post ', 'elon', 'bitcoin', 'crypto', 'trump', 'election',
    'nfl', 'nba', 'mlb', 'nhl', 'cricket', 'rugby', 'ufc', 'boxing',
    'tennis', 'formula 1', 'f1 ', 'golf', 'horse racing',
]


# ─── Sharp odds cache (reused by poisson_trader) ────────────────────────────

_SHARP_CACHE: dict[str, Any] = {}  # {'date': '2026-05-03', 'lookup': {...}}


def get_cached_sharp_odds() -> dict[str, dict]:
    """
    Return today's sharp odds if already fetched, else empty dict.
    Called by poisson_trader to avoid duplicate Odds API calls.
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _SHARP_CACHE.get('date') == today:
        return _SHARP_CACHE.get('lookup', {})
    return {}


def _cache_sharp_odds(lookup: dict[str, dict]) -> None:
    """Store today's sharp odds in module-level cache."""
    _SHARP_CACHE['date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    _SHARP_CACHE['lookup'] = lookup


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DATABASE_URL)


def _serial(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    raise TypeError(f'Not serialisable: {type(obj)}')


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, retries: int = 2, timeout: int = 8) -> Any:
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries:
                raise RuntimeError(f'GET {url} failed: {e}') from e
            time.sleep(delay)
            delay *= 2.0


# ─── Step 1: Fetch today's PM football events ─────────────────────────────────

def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _yes_price_from_market(mkt: dict) -> float | None:
    raw = mkt.get('outcomePrices') or mkt.get('outcome_prices')
    if raw:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        try:
            return float(prices[0])
        except (TypeError, ValueError, IndexError):
            pass
    for t in (mkt.get('tokens') or []):
        if isinstance(t, dict) and (t.get('outcome', '') or '').lower() in ('yes', '1'):
            try:
                return float(t['price'])
            except (KeyError, TypeError, ValueError):
                pass
    return None


def _yes_price(market: dict) -> float | None:
    return _yes_price_from_market(market)


def _no_price(market: dict) -> float | None:
    """Return the 'No' side price for binary PM markets."""
    raw = market.get('outcomePrices') or market.get('outcome_prices')
    if raw:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        try:
            return float(prices[1])
        except (TypeError, ValueError, IndexError):
            pass
    yes = _yes_price(market)
    return round(1.0 - yes, 6) if yes is not None else None


def _is_1x2_market(question: str) -> bool:
    """
    Return True only for plain 1X2 markets (home win / draw / away win).
    Filters out exact score, O/U, halftime, spread, and other derivative markets.
    """
    q = question.lower()
    # Reject derivative market types
    reject_patterns = [
        r'\bby\s+\d',        # "win by 2-0"
        r'(?<!-)\b\d{1,2}\s*-\s*\d{1,2}\b(?![\d-])',  # scorelines like 2-0, 3-1 (not dates like 2026-05-03)
        r'exact',            # exact score
        r'halftime',         # half-time
        r'half.?time',
        r'\b(1st|2nd|first|second)\s+half\b',  # period sub-markets ("Second half draw?")
        r'over\s*[\d.]',    # Over 2.5
        r'under\s*[\d.]',   # Under 2.5
        r'spread',           # handicap spread
        r'handicap',
        r'btts',
        r'both teams',
        r'clean sheet',
        r'first goal',
        r'anytime',
        r'goalscorer',
        r'corners',
        r'cards',
        r'total goals',
        r'to score',         # "Neither team to score", "X to score first"
        r'neither',
    ]
    for pat in reject_patterns:
        if re.search(pat, q):
            return False
    # Accept: "Will X win?", "Draw?", "Will it be a draw?"
    accept_patterns = [
        r'^will .{2,50} win',
        r'\bdraw\b',
        r'^will .{2,50} (beat|defeat)',
    ]
    return any(re.search(p, q) for p in accept_patterns)


def _is_totals_market(question: str) -> bool:
    """Return True for Over/Under goals and Both Teams to Score markets."""
    q = question.lower()
    reject_patterns = [
        r'corners',
        r'cards',
        r'halftime',
        r'half.?time',
        r'\b(1st|2nd|first|second)\s+half\b',  # period totals ("1st Half O/U 1.5")
        r'spread',
        r'exact',
        r'goalscorer',
        r'anytime',
        r'first goal',
    ]
    for pat in reject_patterns:
        if re.search(pat, q):
            return False
    return bool(re.search(r'o/u\s*[\d.]|over\s*[\d.]|under\s*[\d.]|both teams to score|btts', q))


def _extract_home_away_from_event_title(title: str) -> tuple[str, str] | None:
    """
    Parse event titles like 'FC Porto vs. FC Alverca' or 'FC Porto vs. FC Alverca - More Markets'.
    Returns (home, away) or None.
    """
    # Strip suffix like " - More Markets", " - Halftime Result", " - Exact Score"
    clean = re.sub(r'\s+-\s+(?:More Markets|Halftime Result|Exact Score|.*Markets.*)$',
                   '', title, flags=re.IGNORECASE).strip()
    m = re.match(r'^(.+?)\s+vs\.?\s+(.+)$', clean, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def fetch_pm_markets_today() -> list[dict]:
    """
    Fetch today's match markets from Polymarket via /events with date-range filter.

    IMPORTANT: The Gamma API only returns restricted/negRisk events (which includes
    all individual match markets) when end_date_min/max use simple YYYY-MM-DD format.
    ISO timestamps cause the API to silently exclude these events.
    The tag_slug parameter is also unreliable and omitted.

    Paginates through all results (100 per page) to capture the full set.

    Returns flat list of market dicts, each enriched with:
      _yes_price, _no_price, _resolution_time, _home_team, _away_team, _event_title
    """
    log.info('[paper_trader] Fetching Polymarket match events...')

    now = datetime.now(timezone.utc)
    date_min = now.strftime('%Y-%m-%d')
    end_cutoff = now + timedelta(days=max(DAYS_AHEAD, 1))
    date_max = end_cutoff.strftime('%Y-%m-%d')

    events: list[dict] = []
    page_size = 100
    for offset in range(0, 2000, page_size):
        try:
            page = _get(f'{GAMMA_API}/events', params={
                'closed':       'false',
                'active':       'true',
                'limit':        page_size,
                'offset':       offset,
                'end_date_min': date_min,
                'end_date_max': date_max,
            })
        except RuntimeError as e:
            log.error(f'[paper_trader] Gamma /events error at offset {offset}: {e}')
            break
        if not isinstance(page, list) or not page:
            break
        events.extend(page)

    log.info(f'[paper_trader] Fetched {len(events)} events across date range {date_min} → {date_max}')

    markets_out: list[dict] = []
    seen: set[tuple] = set()   # (event_id, question) dedup

    # Drop women's events — DC/Poisson models are men-only (see dc_scanner._is_women_event).
    try:
        from dc_scanner import _is_women_event as _is_women
        n_raw = len(events)
        events = [e for e in events if not _is_women(e)]
        if n_raw - len(events) > 0:
            log.info(f'[paper_trader] Filtered out {n_raw - len(events)} women\'s events')
    except Exception:
        pass

    for event in events:
        event_title = event.get('title', '')
        end_date    = _parse_dt(event.get('endDate'))
        event_id    = event.get('id', '')

        if end_date is None or end_date < now - timedelta(hours=3):
            continue

        # Extract home/away team names directly from the event title
        teams = _extract_home_away_from_event_title(event_title)
        home_team = teams[0] if teams else None
        away_team = teams[1] if teams else None
        event_tags = [
            t.get('label', '') for t in event.get('tags', [])
            if t.get('label') and t.get('label') not in ('Soccer', 'Sports', 'Games', 'sea')
        ]

        for mkt in event.get('markets', []):
            if not mkt.get('active') or mkt.get('closed'):
                continue

            question = mkt.get('question', '') or event_title
            dedup_key = (event_id, question)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Keep 1X2 markets + O/U totals + BTTS
            if not (_is_1x2_market(question) or _is_totals_market(question)):
                continue

            yes_p = _yes_price_from_market(mkt)
            if yes_p is None or yes_p <= 0.03 or yes_p >= 0.97:
                continue

            no_p = round(1.0 - yes_p, 6)

            mkt['_yes_price']       = yes_p
            mkt['_no_price']        = no_p if 0.03 < no_p < 0.97 else None
            mkt['_resolution_time'] = end_date
            mkt['_home_team']       = home_team
            mkt['_away_team']       = away_team
            mkt['_event_title']     = event_title
            mkt['_event_tags']      = event_tags
            mkt['question']         = question
            mkt['_live']            = event.get('live', False)
            mkt['_ended']           = event.get('ended', False)
            mkt['_score']           = event.get('score')
            mkt['_elapsed']         = event.get('elapsed')
            mkt['_period']          = event.get('period')
            markets_out.append(mkt)

    window = 'today' if DAYS_AHEAD == 0 else f'next {DAYS_AHEAD} day(s)'
    n_events = len({m['_event_title'] for m in markets_out})
    log.info(f'[paper_trader] {len(markets_out)} markets across {n_events} events ({window})')
    return markets_out


# ─── Step 2: Fetch today's sharp odds from The Odds API ───────────────────────

def fetch_sharp_odds_today(
    team_hints: list[str] | None = None,
    tag_hints:  list[str] | None = None,
) -> dict[str, dict]:
    """
    Fetch pre-match h2h odds from The Odds API for today's events.

    Uses PM event tags (tag_hints) as primary sport selector via PM_TAG_TO_SPORT,
    then supplements with team-name heuristics (team_hints).
    Falls back to all TARGET_SPORTS when neither gives coverage.

    Returns {} if no API key or quota exceeded.
    """
    if not ODDS_API_KEY:
        log.warning('[paper_trader] THE_ODDS_API_KEY not set — cannot fetch live sharp odds')
        return {}

    found_sports: set[str] = set()

    # 1. Tag-based selection (most reliable — directly from PM competition tags)
    if tag_hints:
        for tag in tag_hints:
            sport = PM_TAG_TO_SPORT.get(tag)
            if sport:
                found_sports.add(sport)

    # 2. Team-name heuristics (catches top clubs not tagged with a competition)
    TEAM_TO_SPORT: dict[str, str] = {
        'liverpool': 'soccer_epl', 'arsenal': 'soccer_epl', 'chelsea': 'soccer_epl',
        'manchester': 'soccer_epl', 'man city': 'soccer_epl', 'man utd': 'soccer_epl',
        'tottenham': 'soccer_epl', 'newcastle': 'soccer_epl', 'aston villa': 'soccer_epl',
        'wolves': 'soccer_epl', 'brighton': 'soccer_epl', 'nottingham': 'soccer_epl',
        'real madrid': 'soccer_spain_la_liga', 'barcelona': 'soccer_spain_la_liga',
        'atletico': 'soccer_spain_la_liga', 'sevilla': 'soccer_spain_la_liga',
        'villarreal': 'soccer_spain_la_liga', 'betis': 'soccer_spain_la_liga',
        'osasuna': 'soccer_spain_la_liga', 'levante': 'soccer_spain_la_liga',
        'bayern': 'soccer_germany_bundesliga', 'dortmund': 'soccer_germany_bundesliga',
        'leverkusen': 'soccer_germany_bundesliga', 'leipzig': 'soccer_germany_bundesliga',
        'frankfurt': 'soccer_germany_bundesliga', 'freiburg': 'soccer_germany_bundesliga',
        'juventus': 'soccer_italy_serie_a', 'inter': 'soccer_italy_serie_a',
        'milan': 'soccer_italy_serie_a', 'napoli': 'soccer_italy_serie_a',
        'roma': 'soccer_italy_serie_a', 'lazio': 'soccer_italy_serie_a',
        'psg': 'soccer_france_ligue_one', 'marseille': 'soccer_france_ligue_one',
        'lyon': 'soccer_france_ligue_one', 'monaco': 'soccer_france_ligue_one',
        'porto': 'soccer_portugal_primeira_liga', 'benfica': 'soccer_portugal_primeira_liga',
        'sporting': 'soccer_portugal_primeira_liga', 'braga': 'soccer_portugal_primeira_liga',
        'ajax': 'soccer_netherlands_eredivisie', 'psv': 'soccer_netherlands_eredivisie',
        'feyenoord': 'soccer_netherlands_eredivisie',
        'flamengo': 'soccer_brazil_campeonato', 'palmeiras': 'soccer_brazil_campeonato',
        'river': 'soccer_argentina_primera_division', 'boca': 'soccer_argentina_primera_division',
    }
    CL_CLUBS = {'real madrid', 'barcelona', 'arsenal', 'liverpool', 'manchester',
                'chelsea', 'tottenham', 'inter', 'milan', 'juventus', 'bayern', 'dortmund',
                'atletico', 'psg', 'porto', 'benfica', 'ajax', 'psv', 'feyenoord'}

    if team_hints:
        hints_lower = ' '.join(h.lower() for h in team_hints)
        for kw, sport in TEAM_TO_SPORT.items():
            if kw in hints_lower:
                found_sports.add(sport)
        if any(c in hints_lower for c in CL_CLUBS):
            found_sports.add('soccer_uefa_champs_league')
            found_sports.add('soccer_uefa_europa_league')

    # 3. Fallback: fetch everything in TARGET_SPORTS when hints give no coverage
    sports_to_fetch = sorted(found_sports) if found_sports else TARGET_SPORTS

    log.info(f'[paper_trader] Fetching sharp odds ({len(sports_to_fetch)} sport(s))...')
    now = datetime.now(timezone.utc)
    if DAYS_AHEAD == 0:
        cutoff = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        cutoff = (now + timedelta(days=DAYS_AHEAD)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    lookup: dict[str, dict] = {}
    total_events = 0

    for sport in sports_to_fetch:
        try:
            events = _get(f'{ODDS_API_BASE}/sports/{sport}/odds', params={
                'apiKey':     ODDS_API_KEY,
                'regions':    'eu',
                'markets':    'h2h,totals',
                'bookmakers': ','.join(SHARP_BOOKS.keys()),
                'oddsFormat': 'decimal',
            }, timeout=8)
        except RuntimeError as e:
            if '401' in str(e) or '422' in str(e):
                log.warning('[paper_trader] Odds API key invalid or quota exceeded')
                return {}
            log.debug(f'[paper_trader] {sport}: {e}')
            continue

        if not isinstance(events, list):
            continue

        for event in events:
            commence = _parse_dt(event.get('commence_time'))
            if commence is None or not (now <= commence <= cutoff):
                continue

            sharp = _vig_remove(event)
            if sharp is None:
                continue

            home = event['home_team']
            away = event['away_team']
            sharp['home'] = home
            sharp['away'] = away
            sharp['commence_time'] = commence
            sharp['sport'] = sport

            # Store under multiple keys for flexible matching
            for key in _match_keys(home, away):
                lookup[key] = sharp
            total_events += 1

    log.info(f'[paper_trader] Sharp odds loaded: {total_events} events across {len(TARGET_SPORTS)} leagues')
    _cache_sharp_odds(lookup)
    return lookup


def _vig_remove(event: dict) -> dict | None:
    """Vig-remove Pinnacle + Betfair odds → consensus implied probabilities (h2h + totals)."""
    home = event.get('home_team', '')
    away = event.get('away_team', '')
    book_probs: dict[str, dict] = {}
    book_totals: dict[str, dict] = {}

    for bk in event.get('bookmakers', []):
        key = bk['key']
        if key not in SHARP_BOOKS:
            continue
        for mkt in bk.get('markets', []):
            if mkt['key'] == 'h2h':
                raw = {o['name']: float(o['price']) for o in mkt['outcomes']}
                ho = raw.get(home)
                do = raw.get('Draw')
                ao = raw.get(away)
                if not (ho and ao):
                    continue
                h_imp = 1 / ho
                d_imp = (1 / do) if do else 0.0
                a_imp = 1 / ao
                total = h_imp + d_imp + a_imp
                book_probs[key] = {
                    'home': h_imp / total,
                    'draw': d_imp / total if do else None,
                    'away': a_imp / total,
                    'home_odds': ho, 'draw_odds': do, 'away_odds': ao,
                }
            elif mkt['key'] == 'totals':
                over_o = under_o = point = None
                for o in mkt['outcomes']:
                    if o['name'] == 'Over':
                        over_o = float(o['price'])
                        point = float(o.get('point', 0))
                    elif o['name'] == 'Under':
                        under_o = float(o['price'])
                if over_o and under_o and point:
                    imp_over = 1 / over_o
                    imp_under = 1 / under_o
                    tot = imp_over + imp_under
                    book_totals[key] = {
                        'line': point,
                        'over_prob': imp_over / tot,
                        'under_prob': imp_under / tot,
                        'over_odds': over_o,
                        'under_odds': under_o,
                    }

    if not book_probs:
        return None

    def wavg(field):
        vals = [(book_probs[k][field], SHARP_BOOKS[k])
                for k in book_probs if book_probs[k].get(field) is not None]
        if not vals:
            return None
        return sum(v * w for v, w in vals) / sum(w for _, w in vals)

    hp = wavg('home')
    dp = wavg('draw')
    ap = wavg('away')
    if not (hp and ap):
        return None

    total = (hp or 0) + (dp or 0) + (ap or 0)
    result = {
        'home_prob': hp / total,
        'draw_prob': dp / total if dp else None,
        'away_prob': ap / total,
        'sources':   book_probs,
    }

    # Add totals consensus if available
    if book_totals:
        def wavg_totals(field):
            vals = [(book_totals[k][field], SHARP_BOOKS[k])
                    for k in book_totals if book_totals[k].get(field) is not None]
            if not vals:
                return None
            return sum(v * w for v, w in vals) / sum(w for _, w in vals)

        result['totals'] = {}
        for bk_key, td in book_totals.items():
            line = td['line']
            line_key = f'{line:.1f}'.replace('.0', '.0')
            if line_key not in result['totals']:
                result['totals'][line_key] = {
                    'line': line,
                    'over_prob': wavg_totals('over_prob'),
                    'under_prob': wavg_totals('under_prob'),
                }

    return result


# ─── Step 2b: api-football Pinnacle feed (primary sharp source) ───────────────
#
# PM names follow FIFA conventions; api-football uses its own. Only divergences
# that the fuzzy matcher can't absorb need an entry here.
_AF_NATIONAL_ALIASES = {
    'korea republic':       'South Korea',
    'korea dpr':            'North Korea',
    'czechia':              'Czech Republic',
    'united states':        'USA',
    'ir iran':              'Iran',
    "côte d'ivoire":        'Ivory Coast',
    'cote divoire':         'Ivory Coast',
    'türkiye':              'Turkey',
    'turkiye':              'Turkey',
    'china pr':             'China',
    'uae':                  'United Arab Emirates',
    'cabo verde':           'Cape Verde Islands',
}


def _af_team(name: str) -> str:
    return _AF_NATIONAL_ALIASES.get(name.strip().lower(), name)


def _af_event_from_closing(raw: dict, home: str, away: str,
                           kickoff, swapped: bool = False) -> dict | None:
    """Convert closing_collector's de-vigged Pinnacle blob to the lookup event shape."""
    h2h = raw.get('h2h') or {}
    hp, dp, ap = h2h.get('home'), h2h.get('draw'), h2h.get('away')
    if hp is None or ap is None:
        return None
    if swapped:
        hp, ap = ap, hp
    event = {
        'home': home, 'away': away,
        'home_prob': hp, 'draw_prob': dp, 'away_prob': ap,
        'commence_time': kickoff,
        'sport': 'api-football',
        'sources': {'pinnacle': {**h2h, 'swapped': swapped}},
    }
    totals: dict[str, dict] = {}
    for k, over_p in raw.items():
        m = re.match(r'^over_(\d+)_(\d+)$', k)
        if not m:
            continue
        under_p = raw.get(f'under_{m.group(1)}_{m.group(2)}')
        if under_p is None:
            continue
        line = float(f'{m.group(1)}.{m.group(2)}')
        totals[str(line)] = {'line': line, 'over_prob': over_p, 'under_prob': under_p}
    if totals:
        event['totals'] = totals
    return event


def fetch_sharp_odds_apifootball(pm_markets: list[dict]) -> dict[str, dict]:
    """
    Build the sharp lookup from api-football Pinnacle odds (bookmaker id 4).

    PM-board-driven: each unique PM match resolves to an api-football fixture
    (one /fixtures call per date, cached) plus one /odds call per fixture.
    Output events share fetch_sharp_odds_today's shape, so fuzzy matching and
    _sharp_prob_for_outcome work unchanged. Pinnacle-only consensus — the
    api-football "Betfair" (id 3) is the Sportsbook (~4.8% vig), not the
    Exchange, so it would add noise rather than sharpness.
    """
    try:
        import closing_collector as cc
    except ImportError:
        try:
            from . import closing_collector as cc  # type: ignore[no-redef]
        except ImportError:
            return {}
    if not getattr(cc, 'FOOTBALL_API_KEY', ''):
        log.info('[paper_trader] FOOTBALL_API_KEY not set — skipping api-football feed')
        return {}

    # Unique PM matches with a usable home/away pair
    matches: dict[tuple[str, str], str | None] = {}
    for m in pm_markets:
        home, away = m.get('_home_team'), m.get('_away_team')
        if not (home and away):
            two = _extract_two_teams(m.get('question') or m.get('title') or '')
            if two:
                home, away = two
        if not (home and away):
            continue
        if (home, away) not in matches:
            rt = m.get('_resolution_time')
            matches[(home, away)] = (
                rt.strftime('%Y-%m-%d') if isinstance(rt, datetime) else rt
            )
    if not matches:
        return {}

    log.info(f'[paper_trader] api-football sharp feed: resolving {len(matches)} PM matches...')
    fixtures_cache: dict[str, list] = {}
    odds_cache: dict[int, dict | None] = {}
    lookup: dict[str, dict] = {}
    hits = 0

    for (home, away), date_str in matches.items():
        af_home, af_away = _af_team(home), _af_team(away)
        swapped = False
        fixture_id, kickoff = cc._find_fixture(af_home, af_away, date_str, fixtures_cache)
        if not fixture_id:
            # PM event titles are home-first for football, but absorb the odd
            # reversed listing; probs are swapped back to PM orientation.
            fixture_id, kickoff = cc._find_fixture(af_away, af_home, date_str, fixtures_cache)
            swapped = fixture_id is not None
        if not fixture_id:
            log.debug(f'  [af-feed] no fixture: {home} vs {away}')
            continue

        if fixture_id in odds_cache:
            raw = odds_cache[fixture_id]
        else:
            raw = cc._fetch_pinnacle_odds(fixture_id)
            odds_cache[fixture_id] = raw
        if not raw:
            continue

        event = _af_event_from_closing(raw, home, away, kickoff, swapped=swapped)
        if event is None:
            continue
        hits += 1
        for k in _match_keys(home, away):
            lookup[k] = event

    log.info(f'[paper_trader] api-football sharp feed: Pinnacle odds for {hits}/{len(matches)} matches')
    if lookup:
        _cache_sharp_odds(lookup)
    return lookup


def _norm(s: str) -> str:
    """Normalise team name for matching: lowercase, strip punctuation, collapse spaces."""
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(r'\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia|club|de|da|do|dos|la|el|al|cd|ca|cs)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _match_keys(home: str, away: str) -> list[str]:
    """Generate multiple lookup keys for a match to maximise hit rate."""
    hn, an = _norm(home), _norm(away)
    h6, a6 = hn[:6], an[:6]
    return [
        f'{hn}_{an}',
        f'{h6}_{a6}',
        f'{hn[:8]}_{an[:8]}',
    ]


# ─── Step 3: Extract team + outcome from PM market title ─────────────────────

def _extract_team(title: str) -> str | None:
    """
    Extract the primary team name from single-team PM market titles:
      "Will Liverpool FC win on 2026-05-03?" → "Liverpool"
      "Will Real Madrid CF win?" → "Real Madrid"
    """
    t = title.strip()
    if any(kw in t.lower() for kw in NON_FOOTBALL):
        return None

    # "Will X [FC] win [on ...]?"
    m = re.search(r'will\s+(.+?)\s+(?:FC\s+|CF\s+)?win\b', t, re.IGNORECASE)
    if m:
        team = re.sub(r'\s+(?:FC|CF|SC|AC)\s*$', '', m.group(1), flags=re.IGNORECASE).strip()
        # Remove trailing date fragments like "on 2026-05-03"
        team = re.sub(r'\s+on\s+\d{4}-\d{2}-\d{2}.*', '', team).strip()
        return team if len(team) > 2 else None

    return None


def _extract_two_teams(title: str) -> tuple[str, str] | None:
    """Extract home + away from "X vs Y" style titles."""
    if any(kw in title.lower() for kw in NON_FOOTBALL):
        return None
    m = re.search(r'(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:[:\-\?]|$)', title, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.search(r'will\s+(.+?)\s+(?:beat|win\s+vs?\.?)\s+(.+?)[\?\.]', title, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def _classify_outcome(title: str, home: str, away: str) -> tuple[str, str] | None:
    """
    Map market title → (outcome_key, label).
    outcome_key: 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'btts_yes'
    """
    t = title.lower()
    hn = _norm(home)
    an = _norm(away)
    hn6, an6 = hn[:6], an[:6]

    # Period / prop sub-markets — the sharp lookup only prices full-time 1X2 + totals
    if re.search(r'\b(1st|2nd|first|second)\s+half\b|half.?time|to score|neither'
                 r'|spread|handicap|corner|card|penalt|exact', t):
        return None

    if 'draw' in t and 'no draw' not in t:
        return ('draw', 'Draw')

    ou_match = re.search(r'(?:o/u|over|under)\s*([\d.]+)', t)
    if ou_match:
        # Team totals ("X vs Y: Canada O/U 1.5") are not full-match totals
        seg = _norm(t[:ou_match.start()].split(':')[-1])
        if seg and ((hn6 and hn6 in seg) or (an6 and an6 in seg)):
            return None
        line = ou_match.group(1)
        direction = 'under' if 'under' in t else 'over'
        return (f'{direction}_{line}', f'{direction.title()} {line} goals')
    if 'both teams to score' in t or 'btts' in t:
        return ('btts_no' if 'no' in t.split('btts')[-1] else 'btts_yes',
                'BTTS No' if 'no' in t.split('btts')[-1] else 'BTTS Yes')

    t_norm = _norm(t)
    if hn6 in t_norm and 'win' in t:  return ('home', f'{home} win')
    if an6 in t_norm and 'win' in t:  return ('away', f'{away} win')
    if hn6 in t_norm:                 return ('home', f'{home} win')
    return None


# ─── Step 4: Match PM market to Odds API event ────────────────────────────────

def _fuzzy_find_event(team_name: str, sharp_lookup: dict) -> dict | None:
    """
    Given a single team name, find the Odds API event that contains it.
    Tries exact norm match, then prefix match, then substring scan.
    """
    tn = _norm(team_name)
    t6 = tn[:6]

    # Try prefix keys first (fast path)
    for key, event in sharp_lookup.items():
        parts = key.split('_')
        if len(parts) >= 2:
            if tn in key or t6 in key:
                return event

    # Full scan — check home/away team names directly
    for event in sharp_lookup.values():
        hn = _norm(event.get('home', ''))
        an = _norm(event.get('away', ''))
        if tn in hn or hn in tn or tn in an or an in tn:
            return event
        if t6 and (t6 in hn or t6 in an):
            return event

    return None


def _fuzzy_find_event_two(home: str, away: str, sharp_lookup: dict) -> dict | None:
    """Find an Odds API event matching both team names."""
    hn, an = _norm(home), _norm(away)
    h6, a6 = hn[:6], an[:6]

    for key in _match_keys(home, away):
        if key in sharp_lookup:
            return sharp_lookup[key]

    # Full scan
    for event in sharp_lookup.values():
        eh = _norm(event.get('home', ''))
        ea = _norm(event.get('away', ''))
        h_match = hn in eh or eh in hn or (h6 and h6 in eh)
        a_match = an in ea or ea in an or (a6 and a6 in ea)
        if h_match and a_match:
            return event

    return None


# ─── Step 5: Get sharp probability for the specific outcome ───────────────────

def _sharp_prob_for_outcome(outcome_key: str, event: dict) -> float | None:
    """Map outcome_key to the vig-removed probability from the event."""
    if outcome_key == 'home':  return event.get('home_prob')
    if outcome_key == 'draw':  return event.get('draw_prob')
    if outcome_key == 'away':  return event.get('away_prob')

    # Totals: match PM line to sharp line (exact match only)
    totals = event.get('totals', {})
    if not totals:
        return None

    m = re.match(r'(over|under)_([\d.]+)', outcome_key)
    if m:
        direction = m.group(1)  # 'over' or 'under'
        pm_line = m.group(2)    # e.g. '2.5'
        for line_key, td in totals.items():
            sharp_line = td['line']
            if float(pm_line) == sharp_line:
                return td[f'{direction}_prob']
        return None

    return None


# ─── DB: strategy + trade helpers ────────────────────────────────────────────

def _get_or_create_scan_strategy(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies WHERE name = 'PM-vs-Pinnacle Pre-Match' LIMIT 1")
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
        VALUES ('PM Pre-Match Edge Scanner',
                'Polymarket pre-match prices diverge from Pinnacle/Betfair consensus.',
                'PM user base is less sharp; pre-match prices slow to reflect sharp moves.',
                'agent', 'live', 'agent')
        RETURNING id
    """)
    hyp_id = cur.fetchone()[0]
    conn.commit()
    cur.execute("""
        INSERT INTO strategies (hypothesis_id, name, rules, promoted_at)
        VALUES (%s, 'PM-vs-Pinnacle Pre-Match', %s::jsonb, NOW())
        RETURNING id
    """, (hyp_id, json.dumps({
        'edge_threshold_pp': EDGE_THRESHOLD_PP,
        'benchmark': 'Pinnacle + Betfair (The Odds API)',
        'stake': '1u flat', 'phase': 'paper-only',
    })))
    strat_id = cur.fetchone()[0]
    conn.commit()
    return strat_id


def _upsert_pm_market(conn, market: dict) -> int | None:
    ext_id = str(market.get('id') or market.get('conditionId') or '')
    if not ext_id:
        return None
    title = market.get('question') or market.get('title') or ''
    if not title:
        return None
    t = title.lower()
    mtype = ('1x2' if any(x in t for x in ['win', 'beat', 'draw']) else
             'over_under' if ('over' in t or 'under' in t) else 'other')
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pm_markets (platform, external_id, title, market_type,
                                resolution_time, status, raw_metadata, ingested_at)
        VALUES ('polymarket', %s, %s, %s, %s, 'active', %s::jsonb, NOW())
        ON CONFLICT (platform, external_id) DO UPDATE SET
            title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time,
            raw_metadata = EXCLUDED.raw_metadata, ingested_at = NOW()
        RETURNING id
    """, (ext_id, title, mtype, market.get('_resolution_time'),
          json.dumps({k: v for k, v in market.items()
                      if k not in ('_yes_price', '_resolution_time')})))
    row = cur.fetchone()
    return row[0] if row else None


def _write_paper_trade(conn, strategy_id, market_db_id, outcome,
                       entry_price, sharp_prob, sharp_sources,
                       edge_pp, reasoning, match_id=None) -> int | None:
    cur = conn.cursor()
    if market_db_id:
        cur.execute("""
            SELECT id FROM paper_trades
            WHERE market_id = %s AND outcome = %s AND strategy_id = %s
              AND result IS NULL LIMIT 1
        """, (market_db_id, outcome, strategy_id))
        if cur.fetchone():
            return None

    entry_odds = round(1 / entry_price, 4) if entry_price > 0 else None
    sharp_prob_f = float(sharp_prob) if sharp_prob is not None else None
    cur.execute("""
        INSERT INTO paper_trades (
            strategy_id, market_id, match_id, outcome, entry_price, entry_odds,
            model_probability, sharp_consensus_price, sharp_consensus_sources,
            expected_edge, confidence, stake_units, reasoning, placed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
        RETURNING id
    """, (
        strategy_id, market_db_id, match_id, outcome,
        float(entry_price), entry_odds,
        sharp_prob_f, sharp_prob_f,
        json.dumps(sharp_sources, default=_serial),
        round(float(edge_pp) / 100, 6),
        min(round(float(edge_pp) / 15.0, 3), 1.0),
        STAKE_UNITS, reasoning,
    ))
    trade_id = cur.fetchone()[0]
    conn.commit()
    return trade_id


# ─── DB fallback: Pinnacle odds from match_odds table ────────────────────────

def _db_sharp_odds_today(conn) -> dict[str, dict]:
    """
    Fallback when Odds API key is missing.
    Returns same-format lookup from match_odds table for upcoming fixtures.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            th.canonical_name AS home,
            ta.canonical_name AS away,
            m.kickoff_utc     AS commence_time,
            mo.home_odds, mo.draw_odds, mo.away_odds
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN match_odds mo ON mo.match_id = m.id
        JOIN bookmakers b  ON b.id = mo.bookmaker_id
        WHERE m.kickoff_utc BETWEEN NOW() AND NOW() + INTERVAL '2 days'
          AND m.home_score IS NULL
          AND b.name ILIKE '%pinnacle%'
          AND mo.home_odds IS NOT NULL
    """)
    lookup = {}
    for row in cur.fetchall():
        ho, do, ao = float(row['home_odds']), (float(row['draw_odds']) if row['draw_odds'] else None), float(row['away_odds'])
        h_imp = 1 / ho
        d_imp = (1 / do) if do else 0.0
        a_imp = 1 / ao
        total = h_imp + d_imp + a_imp
        event = {
            'home': row['home'], 'away': row['away'],
            'commence_time': row['commence_time'],
            'home_prob': h_imp / total,
            'draw_prob': d_imp / total if do else None,
            'away_prob': a_imp / total,
            'sources': {'pinnacle_db': {'home_odds': ho, 'draw_odds': do, 'away_odds': ao}},
        }
        for key in _match_keys(row['home'], row['away']):
            lookup[key] = event
    if lookup:
        log.info(f'[paper_trader] DB fallback: {len(set(id(v) for v in lookup.values()))} fixtures loaded')
    return lookup


# ─── Main scan ────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> list[dict]:
    """
    Full scan: fetch today's PM markets → compare vs sharp odds → log trades.
    Returns list of trade dicts.
    """
    log.info('\n' + '='*60)
    log.info('[paper_trader] Pre-match scan — %s',
             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    log.info('='*60)

    if not DATABASE_URL:
        log.error('[paper_trader] DATABASE_URL not set')
        return []

    # 1. Fetch today's PM football markets
    pm_markets = fetch_pm_markets_today()
    if not pm_markets:
        log.info('[paper_trader] No PM football markets for today — done')
        return []

    # 2. Fetch today's sharp odds
    #    Extract team name hints + competition tags from PM markets
    team_hints = [
        market.get('question') or market.get('title') or ''
        for market in pm_markets
    ]
    tag_hints: list[str] = []
    for market in pm_markets:
        tag_hints.extend(market.get('_event_tags', []))

    #    api-football Pinnacle first (7500 req/day); The Odds API as fallback
    #    (~200 req/month left); then DB Pinnacle; then model pricer
    global _FAIR_VALUE_SOURCE  # noqa: PLW0603
    _FAIR_VALUE_SOURCE = 'unknown'
    sharp_lookup = fetch_sharp_odds_apifootball(pm_markets)
    if sharp_lookup:
        _FAIR_VALUE_SOURCE = 'af_pinnacle'
    else:
        log.info('[paper_trader] api-football feed empty — trying The Odds API...')
        sharp_lookup = fetch_sharp_odds_today(team_hints=team_hints, tag_hints=tag_hints)
        if sharp_lookup:
            _FAIR_VALUE_SOURCE = 'odds_api'
    if not sharp_lookup:
        log.info('[paper_trader] Odds API unavailable — trying DB fallback...')
        conn_temp = _conn()
        sharp_lookup = _db_sharp_odds_today(conn_temp)
        conn_temp.close()
        if sharp_lookup:
            _cache_sharp_odds(sharp_lookup)
            _FAIR_VALUE_SOURCE = 'db_pinnacle'

    if not sharp_lookup:
        log.info('[paper_trader] No sharp odds from API or DB — trying model pricer...')
        try:
            from . import model_pricer as _mp
            conn_model = _conn()
            try:
                sharp_lookup = _mp.build_lookup(conn_model, pm_markets)
            finally:
                conn_model.close()
            if sharp_lookup:
                _cache_sharp_odds(sharp_lookup)
                _FAIR_VALUE_SOURCE = 'model'
                log.info(
                    f'[paper_trader] Model pricer active — '
                    f'{len(set(id(v) for v in sharp_lookup.values()))} matches priced '
                    f'(threshold raised to {MODEL_EDGE_THRESHOLD_PP}pp)'
                )
        except Exception as e:
            log.error(f'[paper_trader] Model pricer error: {e}')

    if not sharp_lookup:
        log.warning(
            '[paper_trader] No fair-value source available.\n'
            '  → Set THE_ODDS_API_KEY in ingest/.env (free at the-odds-api.com)\n'
            '  → Or run: python3 ingest/stage_a_football_data.py --seasons 2025-26'
        )
        return []

    # 3. Open DB connection for writing
    conn = _conn()
    trades_found: list[dict] = []

    try:
        strategy_id = None if dry_run else _get_or_create_scan_strategy(conn)

        # Use higher threshold when fair-value comes from our own model
        active_threshold = (
            MODEL_EDGE_THRESHOLD_PP if _FAIR_VALUE_SOURCE == 'model'
            else EDGE_THRESHOLD_PP
        )
        source_label = {
            'af_pinnacle': 'Pinnacle (api-football)',
            'odds_api':   'Pinnacle+Betfair (Odds API)',
            'db_pinnacle': 'Pinnacle (DB closing)',
            'model':      'Poisson form model',
        }.get(_FAIR_VALUE_SOURCE, 'unknown')

        log.info(
            f'[paper_trader] Scanning {len(pm_markets)} PM markets '
            f'[source={_FAIR_VALUE_SOURCE}, threshold={active_threshold}pp]...\n'
        )

        # Collect best edge per match, then log only one trade per match
        match_candidates: dict[str, dict] = {}  # match_key → best candidate

        for market in pm_markets:
            title     = market.get('question') or market.get('title') or ''
            yes_price = market['_yes_price']
            res_time  = market['_resolution_time']
            home_pm   = market.get('_home_team')
            away_pm   = market.get('_away_team')

            # 4. Find matching sharp event
            if home_pm and away_pm:
                event = _fuzzy_find_event_two(home_pm, away_pm, sharp_lookup)
            else:
                two_teams = _extract_two_teams(title)
                if two_teams:
                    event = _fuzzy_find_event_two(*two_teams, sharp_lookup)
                else:
                    single_team = _extract_team(title)
                    if not single_team:
                        log.debug(f'  [skip] Cannot parse teams: {title[:60]}')
                        continue
                    event = _fuzzy_find_event(single_team, sharp_lookup)

            if event is None:
                log.debug(f'  [skip] No sharp event match: {title[:60]}')
                continue

            home = event['home']
            away = event['away']
            match_key = f'{home} vs {away}'.lower()

            # 5. Classify PM market outcome
            outcome_info = _classify_outcome(title, home, away)
            if outcome_info is None:
                log.debug(f'  [skip] Cannot classify outcome: {title[:60]}')
                continue
            outcome_key, outcome_label = outcome_info

            # 6. Get sharp probability for this outcome
            sharp_prob = _sharp_prob_for_outcome(outcome_key, event)
            if sharp_prob is None or sharp_prob <= 0:
                log.debug(f'  [skip] No sharp prob for {outcome_key}: {title[:60]}')
                continue

            kickoff = event.get('commence_time')
            kickoff_str = kickoff.strftime('%d %b %H:%M') if kickoff else '?'
            sources    = event.get('sources', {})
            source_str = ', '.join(sources.keys()) if sources else 'unknown'

            # 7. Evaluate both sides: "Yes" (buy) and "No" (sell = buy the opposite)
            sides = []

            # Yes side
            edge_pp_yes = (sharp_prob - yes_price) * 100
            sides.append(('yes', yes_price, sharp_prob, outcome_label, edge_pp_yes))

            # No side — for 1X2 and totals binary markets
            no_price = market.get('_no_price') or _no_price(market)
            if no_price is not None and 0.03 < no_price < 0.97:
                no_sharp_prob = 1.0 - sharp_prob
                no_label = f'NOT {outcome_label}'
                edge_pp_no = (no_sharp_prob - no_price) * 100
                sides.append(('no', no_price, no_sharp_prob, no_label, edge_pp_no))

            for (side, pm_p, sh_p, label, edge_pp) in sides:
                pm_odds    = round(1 / pm_p, 3)
                sharp_odds = round(1 / sh_p, 3)
                sign = ('✅' if edge_pp >= active_threshold else
                        '~' if edge_pp > 1 else
                        '·' if abs(edge_pp) <= 1 else '❌')

                log.info(f'  {sign} {home} vs {away}  [{kickoff_str} UTC]')
                log.info(f'     {label} [{side.upper()}]: PM={pm_p:.3f} ({pm_odds}x) | '
                         f'Model={sh_p:.3f} ({sharp_odds}x) | Edge={edge_pp:+.2f}pp')

                if edge_pp < active_threshold:
                    continue

                # Keep only the best edge per match
                existing = match_candidates.get(match_key)
                if existing and existing['edge_pp'] >= edge_pp:
                    log.info(f'     → Better edge already found for this match ({existing["edge_pp"]:.2f}pp)')
                    continue

                reasoning = (
                    f'Pre-match PM edge ({source_label}).\n'
                    f'Match: {home} vs {away}  [kickoff {kickoff_str} UTC]\n'
                    f'Selection: {label} [{side.upper()}]\n\n'
                    f'PM price:    {pm_p:.4f}  (odds {pm_odds})\n'
                    f'Fair value:  {sh_p:.4f}  (odds {sharp_odds})\n'
                    f'Edge:        +{edge_pp:.2f}pp\n'
                    f'Source:      {source_str}\n'
                    f'Threshold:   {active_threshold}pp\n\n'
                    f'PM market:  {title}\n'
                    f'Resolves:   {res_time.strftime("%Y-%m-%d %H:%M UTC")}\n'
                )

                match_candidates[match_key] = {
                    'market': market, 'event': event,
                    'label': label, 'side': side,
                    'pm_p': pm_p, 'pm_odds': pm_odds,
                    'sh_p': sh_p, 'sharp_odds': sharp_odds,
                    'edge_pp': edge_pp, 'sources': sources,
                    'source_str': source_str, 'reasoning': reasoning,
                    'title': title, 'res_time': res_time,
                    'home': home, 'away': away, 'kickoff_str': kickoff_str,
                }

        # 8. Log one trade per match (best edge only)
        for match_key, c in match_candidates.items():
            trade_info = {
                'match':      f'{c["home"]} vs {c["away"]}',
                'kickoff':    c['kickoff_str'],
                'outcome':    c['label'],
                'side':       c['side'],
                'pm_price':   c['pm_p'],
                'pm_odds':    c['pm_odds'],
                'sharp_prob': round(c['sh_p'], 4),
                'sharp_odds': c['sharp_odds'],
                'edge_pp':    round(c['edge_pp'], 2),
                'sources':    c['source_str'],
                'pm_market':  c['title'],
            }
            trades_found.append(trade_info)

            if not dry_run:
                market_db_id = _upsert_pm_market(conn, c['market'])
                kickoff_dt = c['event'].get('commence_time')
                kd = kickoff_dt.date() if hasattr(kickoff_dt, 'date') else None
                db_match_id = find_match_id(conn, c['home'], c['away'], kd)
                trade_id = _write_paper_trade(
                    conn, strategy_id, market_db_id,
                    c['label'], c['pm_p'], c['sh_p'],
                    c['sources'], c['edge_pp'], c['reasoning'],
                    match_id=db_match_id,
                )
                if trade_id:
                    log.info(f'  → {c["home"]} vs {c["away"]}: trade #{trade_id} logged (best edge +{c["edge_pp"]:.1f}pp) ✅')
                    trade_info['trade_id'] = trade_id
                else:
                    log.info(f'  → {c["home"]} vs {c["away"]}: skipped (already traded)')

    finally:
        conn.close()

    log.info(f'\n[paper_trader] Done — {len(trades_found)} edge(s) found today')
    return trades_found


def scan_and_print() -> list[dict]:
    """Dry-run alias."""
    return run(dry_run=True)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Paper Trader — today\'s PM vs sharp odds')
    parser.add_argument('--dry-run', action='store_true', help='Print only, no DB writes')
    parser.add_argument('--days', type=int, default=DAYS_AHEAD,
                        help=f'Days ahead to scan (default {DAYS_AHEAD})')
    args = parser.parse_args()
    DAYS_AHEAD = args.days
    trades = run(dry_run=args.dry_run)
    if trades:
        print(f'\n{"─"*60}')
        print(f'EDGES FOUND ({len(trades)}):')
        for t in trades:
            print(f"  {t['match']} | {t['outcome']} | "
                  f"PM={t['pm_price']:.3f} | Sharp={t['sharp_prob']:.4f} | "
                  f"Edge={t['edge_pp']:+.2f}pp")
    else:
        print('\nNo edges found today.')
