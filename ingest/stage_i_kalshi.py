#!/usr/bin/env python3
"""
Stage I: Kalshi football market ingestion.

Mirror of stage_c_polymarket.py for the second venue. Discovers soccer series
via the Kalshi public API, walks their open events, and writes markets +
price snapshots into the SAME tables as Polymarket (pm_markets with
platform='kalshi', pm_market_snapshots).

Read-only against Kalshi: market data and orderbooks are public, no API key,
no account. Nothing here can place an order.

Kalshi taxonomy vs ours:

    Series (KXEPLGAME)  →  Event (one match)  →  Market (one outcome)

An event is one fixture; its markets are the outcomes. So a 1X2 fixture is one
event with three markets (home / tie / away). We store one pm_markets row per
*market* (per outcome), keyed on the market ticker, matching how the PM
scanners already treat single-outcome markets.

Match linking (pm_markets.match_id) is left NULL, exactly as Stage C does:
`matches` is a historical table fed by Football-Data after the fact, so there
are no future fixtures to link to. Instead every row carries a normalised
`_np` block in raw_metadata (home / away / match_key / side / competition) —
that is the key Phase 2 joins Kalshi against Polymarket on.

Home/away orientation: Kalshi event titles are "Home vs Away". Verified
2026-07-22 against the ESPN scoreboard (Kalshi's own settlement source) on
3/3 UCL qualifier fixtures. Do not assume — re-check if titles ever change
shape, this is exactly the bug that bit the NBA scanner.

Usage:
    # Probe — dump API shape + series discovery, no DB writes (run this first)
    python stage_i_kalshi.py --probe

    # Dry run — discover + parse, no DB writes
    python stage_i_kalshi.py --dry-run

    # Production: ingest 1X2 game markets to DB
    python stage_i_kalshi.py

    # Wider market coverage (totals, BTTS, spreads, first half)
    python stage_i_kalshi.py --families GAME,TOTAL,BTTS,SPREAD

    # Single competition
    python stage_i_kalshi.py --series KXEPLGAME
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

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
KALSHI_API = os.getenv('KALSHI_API', 'https://api.elections.kalshi.com/trade-api/v2').rstrip('/')

PLATFORM = 'kalshi'

# Kalshi series-ticker suffix → our pm_markets.market_type vocabulary.
# Order matters: longest suffix first, so 1HTOTAL beats TOTAL.
FAMILY_SUFFIXES: list[tuple[str, str]] = [
    ('1HTOTAL',   'halftime'),
    ('1HSPREAD',  'halftime'),
    ('1H',        'halftime'),
    ('TEAMTOTAL', 'other'),
    ('GAME',      '1x2'),
    ('TOTAL',     'over_under'),
    ('BTTS',      'btts'),
    ('SPREAD',    'handicap'),
    ('SCORE',     'other'),
    ('MOV',       'other'),
    ('FTTS',      'other'),
    ('ADVANCE',   'winner'),
]

# Default: 1X2 only. That is the market the sharp-consensus path already
# prices, and the one with a direct Polymarket counterpart for Phase 2.
DEFAULT_FAMILIES = ['GAME']

# Kalshi publishes no documented public-endpoint rate limit but does return 429
# under a tight sweep of all 95 game series. 0.25s was not enough.
REQUEST_DELAY_SECONDS = float(os.getenv('KALSHI_REQUEST_DELAY', '0.5'))

CACHE_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def http_get(path: str, params: dict | None = None, max_retries: int = 3):
    """GET with exponential backoff retry on transient failures."""
    url = f'{KALSHI_API}{path}'
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30,
                                headers={'accept': 'application/json'})
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

def market_type_for_series(ticker: str) -> str:
    """Map a series ticker to our market_type vocabulary via its suffix."""
    for suffix, mtype in FAMILY_SUFFIXES:
        if ticker.endswith(suffix):
            return mtype
    return 'other'


def family_of_series(ticker: str) -> str | None:
    """The suffix family a series belongs to, or None if it matches nothing."""
    for suffix, _ in FAMILY_SUFFIXES:
        if ticker.endswith(suffix):
            return suffix
    return None


def discover_soccer_series(cache_dir: Path | None, families: list[str],
                           use_cache: bool = True) -> list[dict]:
    """
    All Sports series tagged 'Soccer', restricted to the requested suffix
    families. Kalshi carries ~1,070 soccer series; the family filter is what
    keeps a run to a sane number of requests.
    """
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / 'series_sports.json'
        if use_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                log.debug(f'series cache hit ({int(age)}s old)')
                data = json.loads(cache_file.read_text())
                return _filter_series(data, families)

    log.info(f'fetching series from {KALSHI_API}/series (category=Sports)')
    data = http_get('/series', params={'category': 'Sports'})
    series = data.get('series', []) if isinstance(data, dict) else []

    if cache_file is not None:
        cache_file.write_text(json.dumps(series, indent=2))

    return _filter_series(series, families)


def _filter_series(series: list[dict], families: list[str]) -> list[dict]:
    """Tag-filtered soccer series in the requested families.

    Filter on the `tags` array, never on a substring of the JSON blob — a
    naive text match pulls in things like a Diana Ross NYE market that happens
    to carry a stray 'Soccer' tag alongside 'Music'.
    """
    out = []
    for s in series:
        if s.get('category') != 'Sports':
            continue
        if 'Soccer' not in (s.get('tags') or []):
            continue
        fam = family_of_series(s.get('ticker', ''))
        if fam is None or fam not in families:
            continue
        out.append(s)
    return sorted(out, key=lambda s: s['ticker'])


def fetch_events(series_ticker: str, status: str = 'open',
                 page_limit: int = 200) -> list[dict]:
    """All events for a series, with nested markets, following the cursor."""
    events: list[dict] = []
    cursor = ''
    while True:
        params = {
            'series_ticker': series_ticker,
            'status': status,
            'limit': page_limit,
            'with_nested_markets': 'true',
        }
        if cursor:
            params['cursor'] = cursor
        data = http_get('/events', params=params)
        page = data.get('events', [])
        events.extend(page)
        cursor = data.get('cursor') or ''
        if not cursor or not page:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return events


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Normalise a team name for cross-venue matching.

    Same shape as paper_trader._norm so keys generated here line up with the
    ones the Polymarket side already produces.
    """
    s = (s or '').lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(r'\b(fc|cf|sc|ac|ss|afc|bsc|1\.|vfb|vfl|rb|sv|fk|sk|bv|borussia|club|de|da|do|dos|la|el|al|cd|ca|cs)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def split_teams(event_title: str) -> tuple[str, str] | None:
    """"Home vs Away" → (home, away). See module docstring on orientation."""
    m = re.match(r'^\s*(.+?)\s+vs\.?\s+(.+?)\s*$', event_title or '', re.IGNORECASE)
    if not m:
        return None
    home, away = m.group(1).strip(), m.group(2).strip()
    if not home or not away:
        return None
    return home, away


def match_key(home: str, away: str) -> str:
    """Join key against the Polymarket side. Normalised, home-first."""
    return f'{_norm(home)}_{_norm(away)}'


def _strip_period_prefix(label: str) -> str:
    """'Reg Time: NK Celje' → 'NK Celje'."""
    return re.sub(r'^\s*(reg\s*time|1st\s*half|2nd\s*half|full\s*time)\s*:\s*',
                  '', label or '', flags=re.IGNORECASE).strip()


def classify_outcome(market: dict, home: str | None, away: str | None) -> str:
    """
    Kalshi outcome label → our vocabulary.

    For 1X2 games returns 'home' | 'draw' | 'away'. For anything else returns
    the cleaned Kalshi sub-title, so the raw label survives for market types
    we have not mapped yet.
    """
    label = _strip_period_prefix(market.get('yes_sub_title') or '')
    if not label:
        return 'unknown'

    low = label.lower()
    if low in ('tie', 'draw'):
        return 'draw'

    if home and away:
        ln = _norm(label)
        if ln and ln == _norm(home):
            return 'home'
        if ln and ln == _norm(away):
            return 'away'
        # Kalshi occasionally shortens the club name relative to the title
        # ("Egnatia" vs "Egnatia Rrogozhine"); fall back to a prefix test.
        if ln and (_norm(home).startswith(ln) or ln.startswith(_norm(home))):
            return 'home'
        if ln and (_norm(away).startswith(ln) or ln.startswith(_norm(away))):
            return 'away'

    return label


def _parse_iso_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _safe_float(v) -> float | None:
    """Kalshi returns the *_dollars and *_fp fields as decimal strings."""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_status(market: dict) -> str:
    """Kalshi status → our pm_markets.status vocabulary."""
    s = (market.get('status') or '').lower()
    if s in ('settled', 'finalized', 'determined'):
        return 'closed'
    if s in ('active', 'open'):
        return 'active'
    if s in ('initialized', 'unopened'):
        return 'inactive'
    return s or 'unknown'


def extract_prices(market: dict) -> tuple[float | None, float | None, float | None]:
    """(price, best_bid, best_ask) in [0,1] from the YES side.

    `price` is the mid where both sides quote, otherwise whichever side does.
    Note the legacy integer-cent fields (`yes_bid`, `yes_ask`, `liquidity`)
    now come back as null — the live ones are the `_dollars` variants.
    """
    bid = _safe_float(market.get('yes_bid_dollars'))
    ask = _safe_float(market.get('yes_ask_dollars'))
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    elif bid is not None:
        mid = bid
    elif ask is not None:
        mid = ask
    else:
        mid = _safe_float(market.get('last_price_dollars'))
    return mid, bid, ask


def build_np_block(event: dict, market: dict, home: str | None,
                   away: str | None, outcome: str) -> dict:
    """
    Normalised join block stored in raw_metadata._np.

    This is what Phase 2 joins Kalshi against Polymarket on — there is no
    shared id between the venues, so the key is (normalised teams, side).

    `spread` is carried deliberately: a market with no real book still quotes,
    typically 0.02 / 0.81 on every outcome, which produces a mid that looks
    like a price and is not one. Downstream must filter on spread rather than
    trust the mid.
    """
    meta = event.get('product_metadata') or {}
    _, bid, ask = extract_prices(market)
    return {
        'spread':       round(ask - bid, 4) if (bid is not None and ask is not None) else None,
        'venue':        PLATFORM,
        'home':         home,
        'away':         away,
        'match_key':    match_key(home, away) if home and away else None,
        'side':         outcome,
        'competition':  meta.get('competition'),
        'series':       event.get('series_ticker'),
        'event_ticker': event.get('event_ticker'),
        'close_time':   market.get('close_time'),
        'orientation':  'home_first',   # verified vs ESPN 2026-07-22
    }


# ---------------------------------------------------------------------------
# DB upserts
# ---------------------------------------------------------------------------

def upsert_kalshi_market(cur, event: dict, market: dict, series: dict,
                         observed_at: datetime) -> tuple[int | None, str]:
    """
    Upsert one Kalshi market (= one outcome) into pm_markets.

    Returns (db_id, outcome). db_id is None when the row is unusable.
    """
    external_id = market.get('ticker')
    if not external_id:
        return None, ''

    title = event.get('title') or market.get('title') or ''
    if not title:
        return None, ''

    teams = split_teams(title)
    home, away = teams if teams else (None, None)
    outcome = classify_outcome(market, home, away)

    meta = dict(market)
    meta['_event'] = {k: v for k, v in event.items() if k != 'markets'}
    meta['_np'] = build_np_block(event, market, home, away, outcome)

    cur.execute(
        '''INSERT INTO pm_markets (
              platform, external_id, title, description,
              resolution_criteria, category, market_type,
              created_at_source, resolution_time, status,
              resolved_outcome, resolved_at,
              raw_metadata, ingested_at
           ) VALUES (
              %s, %s, %s, %s,
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
            PLATFORM,
            external_id,
            title,
            market.get('title'),
            market.get('rules_primary'),
            'football',
            market_type_for_series(series.get('ticker', '')),
            _parse_iso_dt(market.get('open_time')),
            _parse_iso_dt(market.get('close_time')),
            parse_status(market),
            None,   # resolved_outcome — set on resolution, not at discovery
            None,   # resolved_at
            json.dumps(meta, default=str),
            observed_at,
        ),
    )
    row = cur.fetchone()
    return (row[0] if row else None), outcome


def insert_snapshot(cur, market_db_id: int, outcome: str, market: dict,
                    observed_at: datetime) -> bool:
    """One snapshot row per (market, outcome, observed_at). Needs a price."""
    price, bid, ask = extract_prices(market)
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
            bid, ask,
            _safe_float(market.get('volume_24h_fp')),
            _safe_float(market.get('open_interest_fp')),
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
# Collection
# ---------------------------------------------------------------------------

def collect(series_list: list[dict], max_series: int | None = None) -> list[tuple[dict, dict, dict]]:
    """Walk the series and return flat (series, event, market) triples."""
    rows: list[tuple[dict, dict, dict]] = []
    todo = series_list[:max_series] if max_series else series_list
    for i, s in enumerate(todo, 1):
        ticker = s['ticker']
        try:
            events = fetch_events(ticker)
        except RuntimeError as e:
            log.warning(f'[{i}/{len(todo)}] {ticker}: fetch failed — {e}')
            continue
        if events:
            n_mkts = sum(len(e.get('markets') or []) for e in events)
            log.info(f'[{i}/{len(todo)}] {ticker}: {len(events)} events, {n_mkts} markets')
        for e in events:
            for m in (e.get('markets') or []):
                rows.append((s, e, m))
        time.sleep(REQUEST_DELAY_SECONDS)
    return rows


# ---------------------------------------------------------------------------
# Probe — first-run discovery
# ---------------------------------------------------------------------------

def probe(cache_dir: Path | None, families: list[str]):
    """Validate API shape and series discovery before trusting any of it."""
    log.info(f'PROBE: {KALSHI_API}')
    all_series = discover_soccer_series(cache_dir, families, use_cache=False)
    log.info(f'PROBE: {len(all_series)} soccer series in families {families}')
    for s in all_series[:20]:
        log.info(f'   {s["ticker"]:28s} {market_type_for_series(s["ticker"]):12s} {s.get("title", "")}')
    if len(all_series) > 20:
        log.info(f'   ... and {len(all_series) - 20} more')

    log.info('PROBE: walking series until one has open events')
    for s in all_series:
        events = fetch_events(s['ticker'])
        if not events:
            continue
        ev = events[0]
        log.info(f'PROBE: sample event from {s["ticker"]}')
        for k in sorted(ev.keys()):
            if k == 'markets':
                continue
            preview = repr(ev[k])
            log.info(f'   {k:22s} : {preview[:137] + "..." if len(preview) > 140 else preview}')
        teams = split_teams(ev.get('title', ''))
        log.info(f'   parsed teams        : {teams}')
        log.info(f'   match_key           : {match_key(*teams) if teams else None}')
        log.info('PROBE: markets on that event:')
        for m in (ev.get('markets') or []):
            price, bid, ask = extract_prices(m)
            oc = classify_outcome(m, *(teams or (None, None)))
            odds = f'{1 / ask:.2f}' if ask else '—'
            log.info(f'   {m.get("ticker"):40s} outcome={oc:8s} bid={bid} ask={ask} '
                     f'mid={price} (ask as odds {odds})')
        return
    log.warning('PROBE: no open events found in any series — most leagues are '
                'out of season in the summer; retry in August or widen --families')


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
    parser.add_argument('--families', type=str, default=','.join(DEFAULT_FAMILIES),
                        help='series suffix families to ingest '
                             f'(default {",".join(DEFAULT_FAMILIES)}; '
                             f'available: {",".join(s for s, _ in FAMILY_SUFFIXES)})')
    parser.add_argument('--series', type=str, default=None,
                        help='restrict to a single series ticker, e.g. KXEPLGAME')
    parser.add_argument('--max-series', type=int, default=None,
                        help='cap how many series to walk (debugging)')
    parser.add_argument('--cache-dir', type=Path, default=Path('.cache/kalshi'),
                        help='cache directory (default: .cache/kalshi)')
    parser.add_argument('--no-cache', action='store_true',
                        help='bypass local cache')
    args = parser.parse_args()

    families = [f.strip().upper() for f in args.families.split(',') if f.strip()]
    known = {s for s, _ in FAMILY_SUFFIXES}
    unknown = [f for f in families if f not in known]
    if unknown:
        log.error(f'unknown families {unknown}; available: {sorted(known)}')
        sys.exit(2)

    if args.probe:
        probe(args.cache_dir, families)
        return

    series_list = discover_soccer_series(args.cache_dir, families,
                                         use_cache=not args.no_cache)
    if args.series:
        series_list = [s for s in series_list if s['ticker'] == args.series]
        if not series_list:
            log.error(f'series {args.series} not found in families {families}')
            sys.exit(2)
    log.info(f'{len(series_list)} soccer series in families {families}')

    if args.dry_run:
        log.info('DRY RUN (no DB writes)')
        rows = collect(series_list, args.max_series)
        log.info(f'collected {len(rows)} markets')
        shown = 0
        for s, e, m in rows:
            teams = split_teams(e.get('title', ''))
            oc = classify_outcome(m, *(teams or (None, None)))
            price, bid, ask = extract_prices(m)
            if shown < 40:
                log.info(f'  [{market_type_for_series(s["ticker"]):10s}] '
                         f'{e.get("title", "")[:48]:48s} {oc:8s} '
                         f'bid={bid} ask={ask} mid={price}')
                shown += 1
        n_priced = sum(1 for _, _, m in rows if extract_prices(m)[0] is not None)
        n_teams = sum(1 for _, e, _ in rows if split_teams(e.get('title', '')))
        spreads = [ask - bid for _, _, m in rows
                   for _, bid, ask in [extract_prices(m)]
                   if bid is not None and ask is not None]
        n_wide = sum(1 for s in spreads if s > 0.10)
        log.info(f'parsed teams: {n_teams}/{len(rows)}   priced: {n_priced}/{len(rows)}')
        if spreads:
            spreads.sort()
            log.info(f'spread: median {spreads[len(spreads) // 2]:.3f}  '
                     f'unquoted (>0.10): {n_wide}/{len(spreads)}')
        return

    if not DATABASE_URL:
        log.error('DATABASE_URL required (set in ingest/.env)')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    observed_at = datetime.now(timezone.utc)
    try:
        cur = conn.cursor()
        log_id = log_start(cur, PLATFORM,
                           f'kalshi_{"+".join(families)}_{observed_at.isoformat()}')
        conn.commit()

        try:
            rows = collect(series_list, args.max_series)
            log.info(f'collected {len(rows)} markets across {len(series_list)} series')

            n_markets, n_snapshots, n_skipped = 0, 0, 0
            for s, e, m in rows:
                market_db_id, outcome = upsert_kalshi_market(cur, e, m, s, observed_at)
                if market_db_id is None:
                    n_skipped += 1
                    continue
                n_markets += 1
                if insert_snapshot(cur, market_db_id, outcome, m, observed_at):
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
