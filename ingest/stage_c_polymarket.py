#!/usr/bin/env python3
"""
Stage C: Polymarket football market ingestion.

Discovers active football markets on Polymarket via the Gamma API, populates
pm_markets, and captures price snapshots in pm_market_snapshots.

Match mapping (linking pm_markets.match_id -> matches.id) is deliberately
deferred to a separate mapping layer (Step 3 of Phase 1). This script leaves
match_id NULL.

Usage:
    # Probe — fetch 5 markets and dump structure (run this first!)
    python stage_c_polymarket.py --probe

    # Dry run — discover + parse, no DB writes
    python stage_c_polymarket.py --dry-run

    # Production: ingest to DB
    python stage_c_polymarket.py

    # Test ingestion with a small batch
    python stage_c_polymarket.py --limit 50

    # Bypass local cache (force fresh fetch)
    python stage_c_polymarket.py --no-cache
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

import psycopg2
import requests
from dotenv import load_dotenv

# Load .env relative to this script's location, regardless of cwd
load_dotenv(Path(__file__).parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv('DATABASE_URL')
GAMMA_API = os.getenv('POLYMARKET_GAMMA_API', 'https://gamma-api.polymarket.com').rstrip('/')
CLOB_API  = os.getenv('POLYMARKET_CLOB_API',  'https://clob.polymarket.com').rstrip('/')


# Heuristic: words that strongly suggest a football/soccer market.
# Filter is multi-signal: title + tags + category + slug. Conservative —
# better to miss a football market than ingest a non-football one (the
# mapping layer downstream relies on this filter being right).
FOOTBALL_KEYWORDS = [
    'soccer', 'football', 'premier league', 'la liga', 'bundesliga',
    'serie a', 'ligue 1', 'champions league', 'europa league', 'epl',
    'mls', 'world cup', 'euro 2', 'fifa', 'uefa',
    # Football-club name fragments that appear in PM titles
    'liverpool', 'arsenal', 'chelsea', 'man city', 'man utd', 'tottenham',
    'real madrid', 'barcelona', 'atletico', 'atlético', 'bayern', 'dortmund',
    'inter milan', 'ac milan', 'juventus', 'napoli', 'psg',
    # Additional top clubs commonly on PM
    'manchester city', 'manchester united', 'newcastle', 'aston villa',
    'bayer leverkusen', 'rb leipzig', 'borussia', 'schalke',
    'inter ', 'milan', 'atalanta', 'fiorentina', 'lazio', 'roma',
    'porto', 'benfica', 'sporting cp', 'ajax', 'psv',
    'celtic', 'rangers', 'marseille', 'lyon', 'monaco',
    'sevilla', 'valencia', 'villarreal', 'athletic bilbao',
]

NON_FOOTBALL_BLOCKERS = [
    'nfl', 'american football', 'super bowl', 'nba', 'mlb', 'nhl',
    'ncaa', 'cricket', 'rugby', 'afl ', 'ufc', 'boxing', 'tennis',
    'formula 1', 'f1 ', 'golf', 'horse racing',
]

# Cache TTL for the markets listing (seconds). Fresh fetch on --no-cache.
CACHE_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def http_get(url: str, params: dict | None = None, max_retries: int = 3):
    """GET with exponential backoff retry on transient failures."""
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt == max_retries:
                break
            log.warning(f'GET {url} attempt {attempt + 1} failed: {e} — retry in {delay}s')
            time.sleep(delay)
            delay *= 2.0
    raise RuntimeError(f'GET {url} failed after {max_retries + 1} attempts: {last_err}')


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def fetch_active_markets(cache_dir: Path | None, limit: int = 500,
                         use_cache: bool = True) -> list:
    """
    Fetch a page of active markets from Gamma API. Single page for now —
    pagination is a future improvement once we know how many football markets
    are typically active at once (probe mode helps establish this).
    """
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f'markets_active_limit{limit}.json'
        if use_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                log.debug(f'cache hit ({int(age)}s old): {cache_file}')
                return json.loads(cache_file.read_text())

    log.info(f'fetching markets from {GAMMA_API}/markets (limit={limit}, sorted by volume)')
    params = {
        'closed':    'false',
        'active':    'true',
        'limit':     limit,
        'order':     'volume24hr',
        'ascending': 'false',
    }
    data = http_get(f'{GAMMA_API}/markets', params=params)

    # Normalise: API may return { "markets": [...] } or [...] directly
    if isinstance(data, dict) and 'markets' in data:
        data = data['markets']
    if not isinstance(data, list):
        log.warning(f'unexpected response shape: {type(data).__name__}')
        return []

    if cache_file is not None:
        cache_file.write_text(json.dumps(data, indent=2))

    return data


def is_football_market(market: dict) -> bool:
    """Multi-signal football detection. Conservative — false negatives OK,
    false positives bad (we don't want non-football trades polluting the agent)."""
    text_parts = [
        market.get('question', '') or '',
        market.get('title', '') or '',
        market.get('description', '') or '',
        market.get('category', '') or '',
        market.get('slug', '') or '',
    ]
    tags = market.get('tags') or []
    for t in tags:
        if isinstance(t, dict):
            text_parts.append(t.get('label', '') or t.get('slug', '') or '')
        else:
            text_parts.append(str(t))
    text = ' '.join(text_parts).lower()

    if any(b in text for b in NON_FOOTBALL_BLOCKERS):
        return False
    return any(kw in text for kw in FOOTBALL_KEYWORDS)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def detect_market_type(market: dict) -> str:
    """Heuristic classification: 1x2, over_under, winner, other."""
    text = (market.get('question', '') or market.get('title', '') or '').lower()
    has_vs   = any(s in text for s in [' vs ', ' v ', ' - '])
    has_ml   = any(o in text for o in ['draw', 'win', 'beat', 'tie', 'moneyline', '1x2'])
    if has_vs and has_ml:
        return '1x2'
    if 'over' in text or 'under' in text or 'total goals' in text:
        return 'over_under'
    if 'winner' in text or 'champion' in text or 'lift' in text:
        return 'winner'
    if 'btts' in text or 'both teams to score' in text:
        return 'btts'
    return 'other'


def _parse_iso_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        s = str(v).replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def parse_resolution_time(market: dict) -> datetime | None:
    for key in ('endDateIso', 'end_date_iso', 'endDate', 'end_date',
                'closeDate', 'closeDateIso'):
        dt = _parse_iso_dt(market.get(key))
        if dt is not None:
            return dt
    return None


def parse_created_at_source(market: dict) -> datetime | None:
    for key in ('createdAt', 'created_at', 'startDate', 'startDateIso'):
        dt = _parse_iso_dt(market.get(key))
        if dt is not None:
            return dt
    return None


def parse_status(market: dict) -> str:
    if market.get('closed') or market.get('archived'):
        return 'closed'
    if market.get('active') is False:
        return 'inactive'
    return 'active'


def _safe_float(v) -> float | None:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_json_array(v):
    """PM sometimes returns 'outcomes'/'outcomePrices' as JSON strings instead of arrays."""
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    return None


def extract_outcomes_with_prices(market: dict) -> list[tuple[str, float | None]]:
    """
    Pull (outcome_label, current_price) tuples. PM exposes outcomes via several
    shapes — try each.

    Returns list of (label, price) where price is in [0, 1] (PM contract price).
    Empty list if nothing parseable.
    """
    # Shape 1: tokens = [{outcome:'Yes', price:0.65, ...}, ...]
    tokens = market.get('tokens') or []
    if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
        out = []
        for t in tokens:
            label = t.get('outcome') or t.get('name')
            if label is not None:
                out.append((str(label), _safe_float(t.get('price'))))
        if out:
            return out

    # Shape 2: outcomes + outcomePrices arrays (sometimes JSON strings)
    outcomes = _safe_json_array(market.get('outcomes'))
    prices   = _safe_json_array(market.get('outcomePrices') or market.get('outcome_prices'))
    if outcomes:
        out = []
        for i, oc in enumerate(outcomes):
            p = _safe_float(prices[i]) if prices and i < len(prices) else None
            out.append((str(oc), p))
        return out

    return []


# ---------------------------------------------------------------------------
# DB upserts
# ---------------------------------------------------------------------------

def upsert_pm_market(cur, market: dict, observed_at: datetime) -> int | None:
    """Upsert into pm_markets; returns the DB id, or None if unparseable."""
    external_id = (
        market.get('id') or market.get('conditionId') or market.get('condition_id')
    )
    if external_id is None:
        return None
    external_id = str(external_id)

    title = market.get('question') or market.get('title') or ''
    if not title:
        return None

    cur.execute(
        '''INSERT INTO pm_markets (
              platform, external_id, title, description,
              resolution_criteria, category, market_type,
              created_at_source, resolution_time, status,
              resolved_outcome, resolved_at,
              raw_metadata, ingested_at
           ) VALUES (
              'polymarket', %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s::jsonb, %s
           )
           ON CONFLICT (platform, external_id) DO UPDATE SET
              title               = EXCLUDED.title,
              description         = EXCLUDED.description,
              resolution_criteria = EXCLUDED.resolution_criteria,
              category            = EXCLUDED.category,
              market_type         = EXCLUDED.market_type,
              resolution_time     = EXCLUDED.resolution_time,
              status              = EXCLUDED.status,
              raw_metadata        = EXCLUDED.raw_metadata,
              ingested_at         = EXCLUDED.ingested_at
           RETURNING id''',
        (
            external_id,
            title,
            market.get('description'),
            market.get('resolutionCriteria') or market.get('resolution_criteria'),
            market.get('category'),
            detect_market_type(market),
            parse_created_at_source(market),
            parse_resolution_time(market),
            parse_status(market),
            None,  # resolved_outcome — populated on resolution, not at discovery
            None,  # resolved_at
            json.dumps(market),
            observed_at,
        ),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_snapshot(cur, market_db_id: int, outcome: str, price: float | None,
                    observed_at: datetime, raw_market: dict) -> bool:
    """One snapshot row per (market, outcome, observed_at). Skip if no price."""
    if price is None:
        return False
    cur.execute(
        '''INSERT INTO pm_market_snapshots (
              market_id, observed_at, outcome, price,
              best_bid, best_ask, volume_24h, open_interest
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT DO NOTHING''',
        (
            market_db_id, observed_at, outcome, price,
            _safe_float(raw_market.get('bestBid')   or raw_market.get('best_bid')),
            _safe_float(raw_market.get('bestAsk')   or raw_market.get('best_ask')),
            _safe_float(raw_market.get('volume24hr') or raw_market.get('volume_24h')),
            _safe_float(raw_market.get('liquidity')  or raw_market.get('open_interest')),
        ),
    )
    return cur.rowcount > 0


def log_start(cur, source: str, resource: str) -> int:
    cur.execute(
        '''INSERT INTO data_ingestion_log (source, resource, status)
           VALUES (%s, %s, 'running') RETURNING id''',
        (source, resource),
    )
    return cur.fetchone()[0]


def log_finish(cur, log_id: int, rows_ingested: int,
               status: str = 'succeeded', error: str | None = None):
    cur.execute(
        '''UPDATE data_ingestion_log
           SET finished_at   = NOW(),
               rows_ingested = %s,
               status        = %s,
               error_message = %s
           WHERE id = %s''',
        (rows_ingested, status, error, log_id),
    )


# ---------------------------------------------------------------------------
# Probe — first-run discovery
# ---------------------------------------------------------------------------

def probe(cache_dir: Path | None, limit: int = 5):
    """Print the structure of the first market returned and run football
    detection on the small batch. Validates API shape before we trust it."""
    log.info(f'PROBE: fetching {limit} markets from {GAMMA_API}/markets')
    markets = fetch_active_markets(cache_dir, limit=limit, use_cache=False)

    if not markets:
        log.warning('PROBE: 0 markets returned. Possible causes:')
        log.warning('  - GAMMA API URL wrong (current: %s)', GAMMA_API)
        log.warning('  - Filters too strict (closed=false, active=true)')
        log.warning('  - PM API rate-limited or down')
        return

    log.info(f'PROBE: got {len(markets)} markets')
    log.info('PROBE: structure of first market (top-level keys + previews):')
    sample = markets[0]
    for k in sorted(sample.keys()):
        v = sample[k]
        preview = repr(v)
        if len(preview) > 140:
            preview = preview[:137] + '...'
        log.info(f'   {k:30s} : {preview}')

    log.info('PROBE: football heuristic on each market:')
    for m in markets:
        title = (m.get('question') or m.get('title') or '<no title>')[:90]
        is_fb = is_football_market(m)
        outcomes = extract_outcomes_with_prices(m)
        oc_str = ', '.join(f'{lbl}={p}' for lbl, p in outcomes[:4]) or '<no outcomes>'
        log.info(f'  [{"FB" if is_fb else "  "}] {title:<90} | {oc_str}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--probe', action='store_true',
                        help='inspect API response structure, no DB writes')
    parser.add_argument('--dry-run', action='store_true',
                        help='discover + parse, no DB writes')
    parser.add_argument('--limit', type=int, default=500,
                        help='max markets per Gamma fetch (default 500)')
    parser.add_argument('--cache-dir', type=Path, default=Path('.cache/polymarket'),
                        help='cache directory (default: .cache/polymarket)')
    parser.add_argument('--no-cache', action='store_true',
                        help='bypass local cache')
    args = parser.parse_args()

    if args.probe:
        probe(args.cache_dir, limit=min(args.limit, 10))
        return

    if args.dry_run:
        log.info('DRY RUN (no DB writes)')
        markets = fetch_active_markets(args.cache_dir, limit=args.limit,
                                       use_cache=not args.no_cache)
        log.info(f'fetched {len(markets)} markets total')
        football = [m for m in markets if is_football_market(m)]
        log.info(f'identified {len(football)} as football')
        for m in football[:30]:
            title = (m.get('question') or m.get('title') or '')[:80]
            outcomes = extract_outcomes_with_prices(m)
            log.info(f'  {title}')
            for lbl, p in outcomes:
                log.info(f'    - {lbl}: {p}')
        if len(football) > 30:
            log.info(f'  ... and {len(football) - 30} more')
        return

    if not DATABASE_URL:
        log.error('DATABASE_URL required (set in ingest/.env)')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    observed_at = datetime.now(timezone.utc)
    try:
        cur = conn.cursor()
        log_id = log_start(cur, 'polymarket',
                           f'gamma_active_{observed_at.isoformat()}')
        conn.commit()

        try:
            markets = fetch_active_markets(args.cache_dir, limit=args.limit,
                                           use_cache=not args.no_cache)
            football = [m for m in markets if is_football_market(m)]
            log.info(f'fetched {len(markets)} markets, '
                     f'{len(football)} look like football')

            n_markets, n_snapshots, n_skipped = 0, 0, 0
            for m in football:
                market_db_id = upsert_pm_market(cur, m, observed_at)
                if market_db_id is None:
                    n_skipped += 1
                    continue
                n_markets += 1
                outcomes = extract_outcomes_with_prices(m)
                if not outcomes:
                    n_skipped += 1
                for lbl, p in outcomes:
                    if insert_snapshot(cur, market_db_id, lbl, p, observed_at, m):
                        n_snapshots += 1

            log_finish(cur, log_id, n_markets)
            conn.commit()
            log.info(f'DONE — markets:{n_markets}  snapshots:{n_snapshots}  '
                     f'skipped:{n_skipped}')
        except Exception as e:
            conn.rollback()
            cur2 = conn.cursor()
            log_finish(cur2, log_id, 0, status='failed', error=str(e))
            conn.commit()
            log.error(f'ingestion failed: {e}')
            raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
