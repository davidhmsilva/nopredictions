#!/usr/bin/env python3
"""
Stage J: what settled football markets cost on Polymarket and Kalshi.

The Lab backtests a theory against Pinnacle's closing line. A prediction-market
user asks a different question -- would this have made money at the price the
VENUE was quoting? -- and until this stage there was no history to answer it:
`pm_ticks` starts 2026-07-21 and records forward only. This stage backfills
every settled football market both venues ever listed, one row per binary
market in `venue_market_history` (db/059): its price at 24h / 6h / 1h before
kick-off and at the close, what takers actually paid, and how it resolved.

    --pm-catalog       Gamma sweep of closed soccer events -> rows, no prices
    --kalshi-catalog   Kalshi GAME / TOTAL / BTTS series, settled -> rows
    --link             each fixture -> our `matches` row, sides, kick-off
    --learn-aliases    club spellings learned from the kick-off clock
                       (venue_team_aliases.json); follow with --relink
    --prices           the price history for every row not yet priced
    --refresh-lab      rebuild what the Lab reads (bt_lab_matches, bt_venue_prices)
    --report           coverage, the join checked against the payouts

With no flag, catalog -> link -> prices -> refresh-lab -> report run in that
order. Every step is idempotent and resumable: a killed run loses at most
one batch.

What was measured before this was written (2026-09-29), each of which
changes what a column means:

  * Polymarket `/prices-history` returns NOTHING for a resolved market under
    `interval=max` -- the reason this project believed only >=12h granularity
    survived resolution. With explicit `startTs`/`endTs` it returns one point a
    minute (fidelity=1) three months after the market closed.
  * Its price is the book MID. 60 of 60 recorded bid/ask pairs agreed to 0.005,
    including a 0.03 / 0.96 placeholder book, which it reports as 0.495. So a
    mid is not a price anyone paid, and an empty book produces a plausible one.
    That is why the row also carries what takers paid.
  * data-api `/trades` takes `end=`, so the last taker buys before kick-off are
    one call. Their price is before the fee.
  * Polymarket's fee is per market and changed twice: none on sports until
    ~2026-03, then 0.03 (`sports_fees_v2`), then 0.05 (`sports_fees_v3`). The
    row carries the market's own rate.
  * Gamma refuses `offset` past ~2,000 ("use /events/keyset"), and a busy
    Saturday has more closed soccer events than that. Some windows also time
    out (Cloudflare 522) at limit=100 and answer at limit=20. Both are handled
    by splitting, never by giving up on the window.
  * 2024-25 events carry no `sportsMarketType` and no `teams`: the families are
    read off the question ("Will X beat Y?", "Will X win on <date>?").
  * Kalshi's candles carry yes_bid / yes_ask per minute -- a real executable
    price, which Polymarket's history does not have. Markets settled before
    Kalshi's cutoff (`/historical/cutoff`) are only under `/historical/...`,
    with field names that drop the `_dollars` suffix.
  * Kalshi publishes no kick-off. Its fixtures take one from the Polymarket
    listing of the same match, else from `matches`.
  * ⚠️ `matches.kickoff_utc` is Football-Data's UK LOCAL time labelled UTC --
    one hour late in British summer time (Liverpool v Bournemouth, 2025-08-15:
    stored 20:00, kicked off 19:00 UTC). Read as UTC it would put the "close"
    an hour into the match. It is converted from Europe/London here, and only
    for the leagues Stage A loads; a time from any other source is never used
    as a close.

Usage:
    cd ingest && source .venv/bin/activate
    python stage_j_venue_history.py --pm-catalog --start 2024-08-01   # ~5 min
    python stage_j_venue_history.py --kalshi-catalog                  # ~5 min
    python stage_j_venue_history.py --link
    python stage_j_venue_history.py --learn-aliases && python stage_j_venue_history.py --relink
    python stage_j_venue_history.py --prices --linked-only            # what the Lab reads first
    python stage_j_venue_history.py --prices                          # everything else
    python stage_j_venue_history.py --report
    python stage_j_venue_history.py --recent-days 5    # daily: every step, last 5 days of PM
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'agent'))

from fixture_match import (  # noqa: E402
    MIN_SIDE_SCORE, _norm_key, is_womens_competition, team_score, tokens,
)
import venues  # noqa: E402

load_dotenv(HERE / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [stage_j] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('stage_j')


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv('DATABASE_URL')

GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
DATA_API = 'https://data-api.polymarket.com'
KALSHI = venues.KALSHI_API

SOCCER_TAG = 100350
PM_START = date(2024, 8, 1)          # the first closed soccer fixture is 2024-08-24

GAMMA_PAGE = 100
GAMMA_OFFSET_CAP = 2000              # offset 2100 -> 422 "use /events/keyset"

# Measured 2026-09-29: prices-history ran 139 req/s with no refusal on 12
# workers; /trades refused 32 of 240 at 85 req/s. Kept well inside both.
# Kalshi's historical candles held ~3.3/s at a 6/s pace, the rest refused as
# 429s and backed off; 4/s is what agent/venues.py measured with no refusals.
CLOB_RATE = 25.0
DATA_API_RATE = 12.0
KALSHI_RATE = float(os.getenv('STAGE_J_KALSHI_RATE', '4.0'))

# A market no one ever traded has a book at best, and usually not that:
# its "mid" is two placeholder orders. Nothing is fetched for it -- on Kalshi,
# whose volume is always reported. Polymarket's volume is missing (NULL) for
# whole months, so there an unknown volume is fetched like a large one.
MIN_VOLUME_FOR_PRICES = 1.0
# Below this, the taker feed is empty or a single stale fill.
MIN_VOLUME_FOR_TRADES = 100.0

# The windows the prices are read at, and how far back a close may reach.
HORIZONS = (('mid_24h', 24 * 3600), ('mid_6h', 6 * 3600), ('mid_1h', 3600))
HISTORY_SPAN_S = 25 * 3600
CLOSE_MAX_AGE_S = 2 * 3600
KALSHI_PLACEHOLDER_SPREAD = 0.10     # the 0.02 / 0.81 books Stage I documents

# Settled means the result is in; the venue flag can lag hours behind it.
SETTLED_AFTER = timedelta(hours=3)

LONDON = ZoneInfo('Europe/London')
ET = ZoneInfo('America/New_York')

# The leagues Stage A loads from Football-Data, whose times are UK local. Only
# these can lend a kick-off to a Kalshi fixture; the Americas and
# internationals come from sources whose times are defaults or dates.
STAGE_A_LEAGUES = {
    'ENG-PR', 'ENG-CH', 'ENG-L1', 'ENG-L2', 'ENG-CON',
    'ESP-LL', 'ESP-L2', 'ITA-SA', 'ITA-SB', 'GER-BL1', 'GER-BL2',
    'FRA-L1', 'FRA-L2', 'NED-ED', 'POR-PL', 'BEL-JPL', 'TUR-SL', 'GRE-SL',
    'SCO-PR', 'SCO-CH', 'SCO-L1', 'SCO-L2',
}

KALSHI_FEE = 0.07


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Pacer:
    """A shared minimum gap between requests to one host."""

    def __init__(self, per_second: float):
        self.gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


_session = threading.local()


def _http() -> requests.Session:
    s = getattr(_session, 's', None)
    if s is None:
        s = requests.Session()
        _session.s = s
    return s


def get_json(url: str, params: dict, pacer: Pacer | None = None,
             tries: int = 4, timeout: float = 30.0):
    """GET -> JSON, retried on 429 / 5xx / network. Raises after `tries`."""
    last: Exception | None = None
    for attempt in range(tries):
        if pacer:
            pacer.wait()
        try:
            r = _http().get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                last = RuntimeError('429')
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code >= 500:
                last = RuntimeError(f'HTTP {r.status_code}')
                time.sleep(1.0 + attempt)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(1.0 + attempt)
    raise RuntimeError(f'{url} {params}: {last}')


CLOB_PACER = Pacer(CLOB_RATE)
DATA_PACER = Pacer(DATA_API_RATE)
KALSHI_PACER = Pacer(KALSHI_RATE)


# ---------------------------------------------------------------------------
# Small parsers (pure; tested in tests/test_stage_j_venue_history.py)
# ---------------------------------------------------------------------------

def parse_ts(raw) -> datetime | None:
    """Gamma writes '2026-09-20 01:15:00+00' and '2026-09-20T01:15:00Z'."""
    if not raw:
        return None
    s = str(raw).strip().replace(' ', 'T', 1)
    s = re.sub(r'Z$', '+00:00', s)
    s = re.sub(r'([+-]\d{2})$', r'\1:00', s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _json_list(v) -> list:
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v or '[]')
    except (TypeError, ValueError):
        return []
    return x if isinstance(x, list) else []


# "EPL: Tottenham vs. Everton " and "UEFA Nations League: Spain vs. Serbia"
# carry the competition in front; "- More Markets" / "- Exact Score" trail
# the sibling events.
_TITLE_PREFIX_RE = re.compile(r'^[^:]{2,60}:\s+(?=.+\s+vs\.?\s+)')
_TITLE_SUFFIX_RE = re.compile(r'\s+-\s+[A-Z][A-Za-z0-9 /\'&.]*$')
_VS_RE = re.compile(r'\s+vs\.?\s+', re.I)


def fixture_teams(title: str) -> tuple[str, str] | None:
    """'EPL: Tottenham vs. Everton ' -> ('Tottenham', 'Everton')."""
    t = (title or '').strip()
    t = _TITLE_PREFIX_RE.sub('', t)
    t = _TITLE_SUFFIX_RE.sub('', t).strip()
    parts = _VS_RE.split(t, maxsplit=1)
    if len(parts) != 2:
        return None
    a, b = parts[0].strip(), parts[1].strip()
    return (a, b) if a and b else None


def which_team(name: str, team_a: str, team_b: str) -> str | None:
    """'a' / 'b' when the name is unambiguously one side, else None.

    A side error does not blunt a moneyline reading, it inverts it, so a name
    that scores for both teams -- or for neither -- is refused.
    """
    sa, sb = team_score(name, team_a), team_score(name, team_b)
    if sa >= MIN_SIDE_SCORE and sa > sb:
        return 'a'
    if sb >= MIN_SIDE_SCORE and sb > sa:
        return 'b'
    return None


_FAMILY_BY_SMT = {
    'moneyline': 'moneyline',
    'totals': 'totals',
    'spreads': 'spreads',
    'both_teams_to_score': 'btts',
}

_DRAW_Q_RE = re.compile(r'\bend in a draw\b', re.I)
_WIN_Q_RE = re.compile(r'^\s*Will\s+(.+?)\s+(?:beat\s+.+|win(?:\s+on\s+.+)?)\?\s*$', re.I)
_OU_RE = re.compile(r'\bO/U\s*(\d+(?:\.\d+)?)\s*$', re.I)
_BTTS_RE = re.compile(r':\s*Both Teams to Score\s*$', re.I)
_SPREAD_Q_RE = re.compile(r'^\s*Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$', re.I)
_SPREAD_GIT_RE = re.compile(r'^\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$')


def _infer_family(q: str) -> str | None:
    """Families for 2024-25 markets, which carry no sportsMarketType."""
    if _DRAW_Q_RE.search(q) or _WIN_Q_RE.match(q):
        return 'moneyline'
    if _OU_RE.search(q) and not re.search(r'half|corner|card|team', q, re.I):
        return 'totals'
    if _BTTS_RE.search(q):
        return 'btts'
    if _SPREAD_Q_RE.match(q):
        return 'spreads'
    return None


def classify_pm_market(m: dict, team_a: str, team_b: str) -> dict | None:
    """One Gamma market -> the family columns of a row, or None to skip it.

    Fails closed: a subject that is not clearly one of the two teams, outcomes
    in an order we do not expect, or a line we cannot read all drop the market.
    """
    smt = m.get('sportsMarketType')
    q = str(m.get('question') or '')
    git = str(m.get('groupItemTitle') or '').strip()
    if smt:
        family = _FAMILY_BY_SMT.get(smt)
    else:
        family = _infer_family(q)
    if family is None:
        return None

    outcomes = [str(o).strip() for o in _json_list(m.get('outcomes'))]
    tokens_ = [str(t) for t in _json_list(m.get('clobTokenIds'))]
    if len(outcomes) != 2 or len(tokens_) != 2:
        return None
    low = [o.lower() for o in outcomes]
    row = {'family': family, 'question': q, 'outcome0': outcomes[0], 'outcome1': outcomes[1],
           'token0': tokens_[0], 'token1': tokens_[1], 'subject': None,
           'subject_team': None, 'line': None}

    if family == 'moneyline':
        if low != ['yes', 'no']:
            return None
        if _DRAW_Q_RE.search(q) or git.lower().startswith('draw'):
            row['subject'], row['subject_team'] = 'draw', 'draw'
            return row
        name = git
        if not name:
            mw = _WIN_Q_RE.match(q)
            name = mw.group(1) if mw else ''
        side = which_team(name, team_a, team_b) if name else None
        if side is None:
            return None
        row['subject'], row['subject_team'] = name, side
        return row

    if family == 'totals':
        if low != ['over', 'under']:
            return None
        line = _num(m.get('line'))
        if line is None:
            mo = _OU_RE.search(q) or _OU_RE.search(git)
            line = _num(mo.group(1)) if mo else None
        if line is None or line <= 0:
            return None
        row['line'] = line
        return row

    if family == 'btts':
        if low != ['yes', 'no']:
            return None
        return row

    # spreads: "Spread: Deportivo Saprissa (-1.5)", outcomes [subject, other]
    ms = _SPREAD_GIT_RE.match(git) or _SPREAD_Q_RE.match(q)
    if not ms:
        return None
    name = ms.group(1)
    line = _num(m.get('line'))
    if line is None:
        line = _num(ms.group(2))
    side = which_team(name, team_a, team_b)
    if side is None or line is None:
        return None
    if which_team(outcomes[0], team_a, team_b) != side:
        return None
    if which_team(outcomes[1], team_a, team_b) != ('b' if side == 'a' else 'a'):
        return None
    row.update(subject=name, subject_team=side, line=line)
    return row


def pm_resolution(m: dict) -> tuple[int | None, float | None]:
    """(winner, payout0) from a closed market, or (None, None) when unresolved.

    Only a clean 1/0, 0/1 or 0.5/0.5 counts. A closed market still carrying a
    trading price is waiting on its resolution, and reading its last price as
    an outcome is the coin flip db/039 refuses to take.
    """
    status = m.get('umaResolutionStatus')
    if status not in (None, 'resolved'):
        return None, None
    px = [_num(p) for p in _json_list(m.get('outcomePrices'))]
    if len(px) != 2 or None in px:
        return None, None
    p0, p1 = px
    if abs(p0 + p1 - 1) > 1e-6:
        return None, None
    for v, w in ((1.0, 0), (0.0, 1), (0.5, None)):
        if abs(p0 - v) < 1e-6:
            return w, v
    return None, None


def pm_volume(m: dict) -> float | None:
    for k in ('volumeNum', 'volume', 'volumeClob'):
        v = _num(m.get(k))
        if v is not None and v > 0:
            return v
    return None


def pm_fee_rate(m: dict) -> float:
    if not m.get('feesEnabled'):
        return 0.0
    sched = m.get('feeSchedule') or {}
    rate = _num(sched.get('rate'))
    return rate if rate is not None else 0.0


def pm_event_rows(e: dict) -> list[dict]:
    """Every priced-family market of one closed Gamma event, as rows."""
    title = str(e.get('title') or '')
    split = fixture_teams(title)
    if not split:
        return []
    team_a, team_b = split
    sides = 'title'
    homes = [t for t in (e.get('teams') or []) if t.get('ordering') == 'home']
    aways = [t for t in (e.get('teams') or []) if t.get('ordering') == 'away']
    if len(homes) == 1 and len(aways) == 1:
        h, a = str(homes[0].get('name') or ''), str(aways[0].get('name') or '')
        if which_team(h, team_a, team_b) == 'a' and which_team(a, team_a, team_b) == 'b':
            sides = 'pm_teams'
        elif which_team(h, team_a, team_b) == 'b' and which_team(a, team_a, team_b) == 'a':
            team_a, team_b = team_b, team_a
            sides = 'pm_teams'

    ev_start = parse_ts(e.get('startTime'))
    ev_date = None
    try:
        ev_date = date.fromisoformat(str(e.get('eventDate'))[:10]) if e.get('eventDate') else None
    except ValueError:
        ev_date = None
    sport = e.get('sport') or {}
    series = e.get('series') or []
    comp_name = sport.get('name') or (series[0].get('title') if series else None)

    rows = []
    for m in e.get('markets') or []:
        if not m.get('closed') or not m.get('conditionId'):
            continue
        fam = classify_pm_market(m, team_a, team_b)
        if fam is None:
            continue
        ko = parse_ts(m.get('gameStartTime'))
        ko_src = 'pm_game_start'
        if ko is None:
            ko, ko_src = ev_start, ('pm_event_start' if ev_start else None)
        if ko is None:
            continue
        winner, payout0 = pm_resolution(m)
        finished = parse_ts(e.get('finishedTimestamp'))
        closed = parse_ts(m.get('closedTime'))
        rows.append({
            'venue': 'polymarket',
            'market_id': m['conditionId'],
            'event_id': str(e.get('id')),
            'sport': 'soccer',
            'competition': e.get('seriesSlug') or (series[0].get('slug') if series else None),
            'competition_name': comp_name,
            'fixture': f'{team_a} vs. {team_b}',
            'team_a': team_a,
            'team_b': team_b,
            'sides_source': sides,
            'event_date': ev_date or ko.date(),
            'kickoff_utc': ko,
            'kickoff_source': ko_src,
            'kickoff_listed': ko,
            'finished_at': finished,
            'closed_at': closed,
            # NULL, not 0, when Gamma carries no volume: it has none on most
            # markets of 2026-03 (Napoli v Torino included), and a missing field
            # read as "never traded" threw away a month of prices.
            'volume_usd': pm_volume(m),
            'fee_rate': pm_fee_rate(m),
            'winner': winner,
            'payout0': payout0,
            'final_score': e.get('score') or None,
            **fam,
        })
    return rows


def horizon_prices(points: list[tuple[int, float]], ko: int) -> dict:
    """{mid_24h, mid_6h, mid_1h, mid_close, close_at} from (ts, price) points.

    Each is the LAST point at or before its moment -- never one after it,
    which would be the future relative to that moment. A close older than
    CLOSE_MAX_AGE_S is refused: a mid from four hours before kick-off is a
    price, but it is not the close.
    """
    pts = sorted((t, p) for t, p in points if t <= ko and p is not None)
    out = {k: None for k, _ in HORIZONS}
    out.update(mid_close=None, close_at=None)
    if not pts:
        return out

    def at_or_before(ts):
        best = None
        for t, p in pts:
            if t <= ts:
                best = (t, p)
            else:
                break
        return best

    for key, back in HORIZONS:
        hit = at_or_before(ko - back)
        # Only a point inside the window it names: a mid from two days out is
        # not the 24h price, and a market listed an hour before kick-off has
        # no 6h price at all.
        if hit and ko - back - hit[0] <= 2 * 3600:
            out[key] = round(hit[1], 4)
    last = pts[-1]
    if ko - last[0] <= CLOSE_MAX_AGE_S:
        out['mid_close'] = round(last[1], 4)
        out['close_at'] = datetime.fromtimestamp(last[0], tz=timezone.utc)
    return out


def last_buys(trades: list[dict], ko: int) -> dict:
    """The last taker BUY of each outcome before kick-off, from /trades rows."""
    out = {'buy0_price': None, 'buy0_at': None, 'buy1_price': None, 'buy1_at': None,
           'trades_1h': 0}
    best: dict[int, tuple[int, float]] = {}
    for t in trades or []:
        ts = t.get('timestamp')
        px = _num(t.get('price'))
        idx = t.get('outcomeIndex')
        if not isinstance(ts, (int, float)) or px is None or ts > ko:
            continue
        if ko - ts <= 3600:
            out['trades_1h'] += 1
        if str(t.get('side') or '').upper() != 'BUY' or idx not in (0, 1):
            continue
        if idx not in best or ts > best[idx][0]:
            best[idx] = (int(ts), px)
    for idx in (0, 1):
        if idx in best:
            ts, px = best[idx]
            out[f'buy{idx}_price'] = round(px, 4)
            out[f'buy{idx}_at'] = datetime.fromtimestamp(ts, tz=timezone.utc)
    return out


def kalshi_candle(c: dict, key: str) -> float | None:
    """A candle's close for `key` ('yes_bid' | 'yes_ask' | 'price').

    Live candles write `close_dollars`; historical ones plain `close`.
    """
    block = c.get(key) or {}
    v = _num(block.get('close_dollars'))
    return v if v is not None else _num(block.get('close'))


def kalshi_mid(c: dict) -> float | None:
    bid, ask = kalshi_candle(c, 'yes_bid'), kalshi_candle(c, 'yes_ask')
    if bid is None or ask is None or ask <= bid or ask - bid > KALSHI_PLACEHOLDER_SPREAD:
        return None
    return round((bid + ask) / 2, 4)


def kalshi_prices(hourly: list[dict], minute: list[dict], ko: int) -> dict:
    """Horizon mids from hourly candles, the close book from minute candles.

    A candle is stamped with the END of its period, so one ending at or before
    a moment holds only what was known by then.
    """
    pts = [(int(c['end_period_ts']), kalshi_mid(c)) for c in hourly
           if isinstance(c.get('end_period_ts'), (int, float))]
    out = horizon_prices([(t, p) for t, p in pts if p is not None], ko)
    out['mid_close'] = out['close_at'] = None
    out['bid_close'] = out['ask_close'] = None
    last = None
    for c in minute:
        ts = c.get('end_period_ts')
        if isinstance(ts, (int, float)) and ts <= ko and (last is None or ts > last['end_period_ts']):
            last = c
    if last is not None and ko - last['end_period_ts'] <= CLOSE_MAX_AGE_S:
        bid, ask = kalshi_candle(last, 'yes_bid'), kalshi_candle(last, 'yes_ask')
        out['bid_close'] = None if bid is None else round(bid, 4)
        out['ask_close'] = None if ask is None else round(ask, 4)
        out['mid_close'] = kalshi_mid(last)
        out['close_at'] = datetime.fromtimestamp(last['end_period_ts'], tz=timezone.utc)
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def connect():
    if not DATABASE_URL:
        sys.exit('DATABASE_URL required (ingest/.env)')
    conn = psycopg2.connect(DATABASE_URL)
    # A bare SELECT under autocommit=False opens a transaction that outlives
    # the read -- the bug agent/db_txn.py exists for. Nothing here needs one.
    conn.autocommit = True
    return conn


CATALOG_COLS = [
    'venue', 'market_id', 'event_id', 'sport', 'competition', 'competition_name',
    'fixture', 'team_a', 'team_b', 'sides_source', 'event_date', 'kickoff_utc',
    'kickoff_source', 'kickoff_listed', 'finished_at', 'closed_at',
    'family', 'question', 'subject', 'subject_team', 'line',
    'outcome0', 'outcome1', 'token0', 'token1', 'volume_usd', 'fee_rate',
    'winner', 'payout0', 'final_score',
]


def upsert_catalog(conn, rows: list[dict]) -> int:
    """Insert or refresh catalog columns. Prices and links are never touched,
    so re-running a sweep costs nothing that was already fetched."""
    if not rows:
        return 0
    dedup = {}
    for r in rows:
        dedup[(r['venue'], r['market_id'])] = r
    vals = [tuple(r.get(c) for c in CATALOG_COLS) for r in dedup.values()]
    # The kick-off a price is read at belongs to --link (settle_kickoff), not
    # to the listing: a refresh updates kickoff_listed and leaves it alone.
    sets = ', '.join(f'{c} = EXCLUDED.{c}' for c in CATALOG_COLS
                     if c not in ('venue', 'market_id', 'kickoff_utc', 'kickoff_source'))
    sets += ', updated_at = now()'
    sql = (f"INSERT INTO venue_market_history ({', '.join(CATALOG_COLS)}) VALUES %s "
           f"ON CONFLICT (venue, market_id) DO UPDATE SET {sets}")
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, vals, page_size=500)
    return len(vals)


# ---------------------------------------------------------------------------
# Polymarket catalog
# ---------------------------------------------------------------------------

class GammaSweep:
    def __init__(self, tag_id: int):
        self.tag_id = tag_id
        self.failed: list[str] = []
        self.requests = 0

    def _page(self, lo: datetime, hi: datetime, offset: int, limit: int) -> list[dict]:
        """One page; a window that times out at `limit` is re-read in slices."""
        params = dict(tag_id=self.tag_id, closed='true', limit=limit, offset=offset,
                      end_date_min=lo.strftime('%Y-%m-%dT%H:%M:%SZ'),
                      end_date_max=hi.strftime('%Y-%m-%dT%H:%M:%SZ'))
        try:
            self.requests += 1
            page = get_json(f'{GAMMA}/events', params, tries=2, timeout=25)
            return page or []
        except RuntimeError:
            if limit == 1:
                self.failed.append(f'{lo.isoformat()} offset {offset}')
                return []
            step = max(1, limit // 5)
            out: list[dict] = []
            for o in range(offset, offset + limit, step):
                pg = self._page(lo, hi, o, step)
                out += pg
                if len(pg) < step:
                    break
            return out

    def window(self, lo: datetime, hi: datetime) -> list[dict]:
        """Every closed event with endDate in [lo, hi]; splits a window too full to page."""
        out: list[dict] = []
        offset = 0
        while True:
            page = self._page(lo, hi, offset, GAMMA_PAGE)
            out += page
            if len(page) < GAMMA_PAGE:
                return out
            offset += GAMMA_PAGE
            if offset > GAMMA_OFFSET_CAP:
                if hi - lo <= timedelta(minutes=10):
                    self.failed.append(f'{lo.isoformat()} over the offset cap')
                    return out
                mid = lo + (hi - lo) / 2
                return self.window(lo, mid) + self.window(mid, hi)


def pm_catalog(conn, start: date, end: date, workers: int = 4) -> None:
    sweep = GammaSweep(SOCCER_TAG)
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    def one_day(day: date) -> tuple[date, int, list[dict]]:
        lo = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        evs = sweep.window(lo, lo + timedelta(days=1) - timedelta(seconds=1))
        rows = []
        for e in evs:
            rows += pm_event_rows(e)
        return day, len(evs), rows

    total_ev = total_rows = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for day, n_ev, rows in ex.map(one_day, days):
            total_ev += n_ev
            total_rows += upsert_catalog(conn, rows)
            if day.day == 1 or n_ev > 1000:
                log.info('%s  events %5d  rows %5d  (cumulative %d events, %d rows)',
                         day, n_ev, len(rows), total_ev, total_rows)
    log.info('Polymarket catalog: %d events, %d market rows, %d Gamma requests',
             total_ev, total_rows, sweep.requests)
    if sweep.failed:
        log.warning('Gamma windows that never answered (%d): %s',
                    len(sweep.failed), sweep.failed[:20])


# ---------------------------------------------------------------------------
# Kalshi catalog
# ---------------------------------------------------------------------------

_KX_FAMILY_RE = re.compile(r'^KX(?P<stem>[A-Z0-9]+?)(?P<fam>GAME|TOTAL|BTTS)$')
# 1HTOTAL / 2HTOTAL / TEAMTOTAL and 1HBTTS / 2HBTTS are other questions, and the
# stem regex would otherwise swallow the half into it.
_KX_NOT_MATCH = re.compile(r'(1H|2H|TEAM)(TOTAL|BTTS)$')


def kalshi_series() -> dict[str, dict[str, str]]:
    """{stem: {'GAME': ticker, 'TOTAL': ..., 'BTTS': ..., 'title': ...}}"""
    data = get_json(f'{KALSHI}/series', {'category': 'Sports'}, KALSHI_PACER)
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for s in (data or {}).get('series') or []:
        t = s.get('ticker') or ''
        if 'Soccer' not in (s.get('tags') or []) or _KX_NOT_MATCH.search(t):
            continue
        m = _KX_FAMILY_RE.match(t)
        if not m:
            continue
        out[m.group('stem')][m.group('fam')] = t
        if m.group('fam') == 'GAME':
            out[m.group('stem')]['title'] = re.sub(r'\s+Game$', '', s.get('title') or t)
    return {k: v for k, v in out.items() if 'GAME' in v}


def _kalshi_pages(path: str, params: dict, key: str = 'markets') -> list[dict]:
    out, cursor = [], None
    for _ in range(200):
        p = dict(params)
        if cursor:
            p['cursor'] = cursor
        d = get_json(f'{KALSHI}{path}', p, KALSHI_PACER) or {}
        out += d.get(key) or []
        cursor = d.get('cursor')
        if not cursor:
            break
    return out


def kalshi_event_titles(series_ticker: str) -> dict[str, str]:
    """event_ticker -> "Home vs Away" for the series' settled events.

    Needed because the market title changed shape: historical markets say
    "Liverpool vs Brentford Winner?", recent ones only "Fulham wins" (seen
    2026-09-29), so the fixture has to come from the event.
    """
    evs = _kalshi_pages('/events', {'series_ticker': series_ticker, 'status': 'settled',
                                    'limit': 200}, key='events')
    return {e['event_ticker']: str(e.get('title') or '') for e in evs if e.get('event_ticker')}


def kalshi_settled(series_ticker: str) -> list[dict]:
    """Every settled market of a series: the historical tier and the live one."""
    hist = _kalshi_pages('/historical/markets', {'series_ticker': series_ticker, 'limit': 1000})
    live = _kalshi_pages('/markets', {'series_ticker': series_ticker, 'status': 'settled',
                                      'limit': 1000})
    seen, out = set(), []
    for m in hist + live:
        if m.get('ticker') and m['ticker'] not in seen:
            seen.add(m['ticker'])
            out.append(m)
    return out


def _kx_suffix(event_ticker: str) -> str:
    return '-'.join((event_ticker or '').split('-')[1:])


def _kx_result(m: dict) -> tuple[int | None, float | None]:
    res = str(m.get('result') or '').lower()
    pay = _num(m.get('settlement_value_dollars'))
    if pay is None:
        pay = _num(m.get('settlement_value'))
        pay = pay / 100 if pay is not None and pay > 1 else pay
    if res == 'yes':
        return 0, 1.0 if pay is None else pay
    if res == 'no':
        return 1, 0.0 if pay is None else pay
    if pay is not None and 0 < pay < 1:
        return None, pay
    return None, None


def kalshi_fixture_rows(stem: str, series: dict[str, str],
                        markets: dict[str, list[dict]],
                        event_titles: dict[str, str] | None = None) -> list[dict]:
    """GAME markets define the fixture; TOTAL / BTTS join it on the ticker suffix.

    The suffix join is keyed on the competition stem too, because two
    competitions can file the same team codes on the same date.
    """
    comp, comp_name = series['GAME'], series.get('title')
    fixtures: dict[str, dict] = {}
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets.get('GAME', []):
        by_event[m.get('event_ticker') or ''].append(m)

    rows: list[dict] = []
    for ev, ms in by_event.items():
        title = (event_titles or {}).get(ev) or \
            re.sub(r'\s+Winner\?\s*$', '', str(ms[0].get('title') or ''), flags=re.I)
        teams = venues.split_vs(title)
        if not teams or not ev:
            continue
        a, b = teams
        et = venues.kalshi_et_date(ev)
        ev_date = datetime.strptime(et, '%Y%m%d').date() if et else None
        base = {
            'venue': 'kalshi', 'event_id': ev, 'sport': 'soccer',
            'competition': comp, 'competition_name': comp_name,
            'fixture': f'{a} vs {b}', 'team_a': a, 'team_b': b,
            'sides_source': 'kalshi_title', 'event_date': ev_date,
            'kickoff_utc': None, 'kickoff_source': None, 'kickoff_listed': None,
            'finished_at': None, 'fee_rate': KALSHI_FEE,
            'final_score': None, 'token0': None, 'token1': None,
        }
        game_rows = []
        for m in ms:
            label = str(m.get('yes_sub_title') or '').strip()
            if venues._DRAW_RE.match(label):
                subject, st = 'draw', 'draw'
            else:
                ha, hb = venues._same_name(label, a), venues._same_name(label, b)
                if ha == hb:
                    game_rows = []
                    break
                subject, st = label, ('a' if ha else 'b')
            winner, payout0 = _kx_result(m)
            game_rows.append({**base, 'market_id': m['ticker'], 'family': 'moneyline',
                              'question': m.get('title'), 'subject': subject,
                              'subject_team': st, 'line': None,
                              'outcome0': 'Yes', 'outcome1': 'No',
                              'volume_usd': _num(m.get('volume_fp')) or _num(m.get('volume')) or 0.0,
                              'closed_at': parse_ts(m.get('close_time')),
                              'winner': winner, 'payout0': payout0})
        # A fixture whose three legs do not read as home / draw / away is a
        # ladder we cannot orient. All of it goes, not the leg that failed.
        sides = sorted(r['subject_team'] for r in game_rows)
        if sides != ['a', 'b', 'draw']:
            continue
        rows += game_rows
        fixtures[_kx_suffix(ev)] = base

    for fam in ('TOTAL', 'BTTS'):
        for m in markets.get(fam, []):
            base = fixtures.get(_kx_suffix(m.get('event_ticker') or ''))
            if base is None:
                continue
            winner, payout0 = _kx_result(m)
            # A totals or BTTS market closes early the moment it is decided
            # (the second goal settles over 1.5), so its close_time is no
            # guide to when the match ended; only the 1X2 legs are.
            row = {**base, 'event_id': base['event_id'], 'market_id': m['ticker'],
                   'question': m.get('title'), 'subject': None, 'subject_team': None,
                   'volume_usd': _num(m.get('volume_fp')) or _num(m.get('volume')) or 0.0,
                   'closed_at': parse_ts(m.get('close_time')),
                   'winner': winner, 'payout0': payout0}
            if fam == 'TOTAL':
                line = _num(m.get('floor_strike'))
                if line is None or not venues._OVER_RE.match(str(m.get('yes_sub_title') or '')):
                    continue
                row.update(family='totals', line=line, outcome0='Over', outcome1='Under')
            else:
                row.update(family='btts', line=None, outcome0='Yes', outcome1='No')
            rows.append(row)
    return rows


def kalshi_catalog(conn) -> None:
    series = kalshi_series()
    log.info('Kalshi: %d soccer competitions with a GAME series', len(series))
    total = 0
    for i, (stem, s) in enumerate(sorted(series.items()), 1):
        markets = {}
        for fam in ('GAME', 'TOTAL', 'BTTS'):
            if fam in s:
                try:
                    markets[fam] = kalshi_settled(s[fam])
                except RuntimeError as e:
                    log.warning('%s: %s', s[fam], e)
                    markets[fam] = []
        try:
            titles = kalshi_event_titles(s['GAME'])
        except RuntimeError as e:
            log.warning('%s events: %s', s['GAME'], e)
            titles = {}
        rows = kalshi_fixture_rows(stem, s, markets, titles)
        total += upsert_catalog(conn, rows)
        if rows:
            log.info('[%d/%d] %-26s game %4d  total %4d  btts %4d  -> %5d rows',
                     i, len(series), s['GAME'], len(markets.get('GAME', [])),
                     len(markets.get('TOTAL', [])), len(markets.get('BTTS', [])), len(rows))
    log.info('Kalshi catalog: %d market rows', total)


# ---------------------------------------------------------------------------
# Linking to `matches`
# ---------------------------------------------------------------------------

LEARNED_ALIASES_PATH = HERE / 'venue_team_aliases.json'


def _load_pm_aliases(include_learned: bool = True) -> dict[str, str]:
    """Venue name -> our canonical name (or __NOT_IN_MODEL__).

    agent/team_aliases.json is hand-curated and wins; venue_team_aliases.json
    is what --learn-aliases found, with its evidence beside each entry. The
    learner itself runs without the learned file, so every run re-derives it
    from the data rather than compounding its own earlier answers.
    """
    out: dict[str, str] = {}
    if include_learned:
        try:
            learned = json.loads(LEARNED_ALIASES_PATH.read_text()).get('aliases') or {}
            out.update({_norm_key(k): v for k, v in learned.items() if isinstance(v, str)})
        except (OSError, ValueError):
            pass
    try:
        raw = json.loads((HERE.parent / 'agent' / 'team_aliases.json').read_text())
        out.update({_norm_key(k): v for k, v in raw.items() if isinstance(v, str)})
    except (OSError, ValueError):
        pass
    return out


NOT_IN_MODEL = '__NOT_IN_MODEL__'


def db_real_kickoff(stored: datetime, league: str) -> datetime | None:
    """A `matches.kickoff_utc` as the instant it really was, or None.

    Football-Data writes UK local time and it is stored as if it were UTC, so
    it is re-read as Europe/London. Midnight is a date without a time, and
    other sources' times are not times at all.
    """
    if league not in STAGE_A_LEAGUES or stored is None:
        return None
    naive = stored.astimezone(timezone.utc).replace(tzinfo=None)
    if naive.hour == 0 and naive.minute == 0:
        return None
    return naive.replace(tzinfo=LONDON).astimezone(timezone.utc)


class TeamIndex:
    """Our teams, searchable by the tokens of every name they are known by."""

    def __init__(self, names: dict[int, list[str]], canonical: dict[int, str],
                 include_learned: bool = True):
        self.names = names
        self.canonical_to_ids: dict[str, set[int]] = defaultdict(set)
        for tid, cn in canonical.items():
            self.canonical_to_ids[_norm_key(cn)].add(tid)
        self.exact: dict[str, set[int]] = defaultdict(set)
        self.prefix: dict[str, set[int]] = defaultdict(set)
        for tid, ns in names.items():
            for n in ns:
                ident, _ = tokens(n)
                for t in ident:
                    self.exact[t].add(tid)
                    for k in (3, 4):
                        if len(t) > k:
                            self.prefix[t[:k]].add(tid)
        self.pm_aliases = _load_pm_aliases(include_learned)
        self._cache: dict[tuple[str, int], float] = {}

    def candidates(self, name: str) -> set[int] | None:
        """Team ids that could be this name; None when the alias table says it
        is not a club we model."""
        alias = self.pm_aliases.get(_norm_key(name))
        if alias == NOT_IN_MODEL:
            return None
        out: set[int] = set()
        if alias:
            out |= self.canonical_to_ids.get(_norm_key(alias), set())
        ident, _ = tokens(name)
        for t in ident:
            out |= self.exact.get(t, set())
            if 3 <= len(t) <= 4:
                out |= self.prefix.get(t, set())
            for k in (3, 4):
                if len(t) > k:
                    out |= self.exact.get(t[:k], set())
        return out

    def score(self, name: str, tid: int) -> float:
        key = (name, tid)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        alias = self.pm_aliases.get(_norm_key(name))
        s = 0.0
        if alias and alias != NOT_IN_MODEL and any(_norm_key(alias) == _norm_key(n)
                                                  for n in self.names.get(tid, [])):
            s = 1.0
        else:
            for n in self.names.get(tid, []):
                s = max(s, team_score(name, n))
                if s >= 1.0:
                    break
        self._cache[key] = s
        return s


# A match runs ~110-125 minutes from kick-off to the final whistle.
MIN_MATCH = timedelta(minutes=100)        # no real kick-off is later than end - 100 min
MAX_LISTED_EARLY = timedelta(hours=5)     # a listed start this long before a tight end is stale
# Kalshi's 1X2 closed 115 / 120 / 124 / 138 / 185 minutes after the real start
# (p1 / p10 / p50 / p90 / p99, 4,225 fixtures with our London-read time,
# 2026-09-29). A start estimated from it sits past p99 on purpose: a close an
# hour stale is a worse price, one read in play is a wrong one.
END_ESTIMATE = timedelta(minutes=190)
# Polymarket's finishedTimestamp is NOT a tight end: 112 / 115 / 132 / 321 /
# 484 minutes after the real start at the same percentiles (5,009 fixtures).
# It is stamped hours late often enough that a start estimated from it would
# land after the match, so it only ever rules a listed start out.
PM_END_WINDOW = timedelta(hours=9)


def settle_kickoff(listed: datetime | None, db_real: datetime | None,
                   end_hint: datetime | None, end_bound: datetime | None = None
                   ) -> tuple[datetime | None, str | None]:
    """(kick-off, source): the instant a pre-match close is read at.

    A venue's listed start is NOT updated when a match is rescheduled. Celta v
    Real Madrid (2026-03) was listed for the 7th at 20:00 and was played and
    finished on the 6th; a close read at the listed time is the result, priced
    at 0.0005. Osasuna v Mallorca was listed 17 hours before it was played. So
    the end of the match is the arbiter wherever it is known:

      end_hint   a TIGHT end -- the moment Kalshi declared the 1X2 winner. It
                 can place a start (END_ESTIMATE before it) and can call a
                 listing stale.
      end_bound  any time after the end -- Polymarket's finishedTimestamp,
                 often stamped hours late, or a market's closedTime. It can
                 only rule a start OUT, never place one.

    In order: our own London-read time, if the end allows it; the listed time,
    if the end allows it ('listed', or 'listed_unchecked' with no end known at
    all); an estimate from a tight end; nothing.

    A stale-EARLY listing that only a loose end could expose (Osasuna, on
    Polymarket alone) is kept: its close is hours old, which is a worse price
    but never a price from inside the match.
    """
    uppers = [t - MIN_MATCH for t in (end_hint, end_bound) if t is not None]
    upper = min(uppers) if uppers else None
    if db_real is not None and (upper is None or db_real <= upper):
        return db_real, 'match_london'
    if listed is not None and (upper is None or listed <= upper) and \
            (end_hint is None or listed >= end_hint - MAX_LISTED_EARLY):
        return listed, ('listed' if uppers else 'listed_unchecked')
    if end_hint is not None:
        return end_hint - END_ESTIMATE, 'end_estimate'
    return None, None


# Our `matches` holds no women's football, so a women's fixture can only ever
# link wrongly -- and did: "Birmingham City WFC" and "Crystal Palace" were
# once learned as Coventry and Man United, off women's fixtures that happened
# to share a kick-off with the men's. "WFC" is not a squad marker the shared
# matcher knows, so the team names are checked here as well.
_WOMENS_TEAM_RE = re.compile(r'\b(wfc|women|womens|ladies|femenino|feminino|feminine|frauen|w)\b|\(w\)',
                             re.I)


def is_womens_fixture(competition: str | None, competition_name: str | None,
                      team_a: str | None, team_b: str | None) -> bool:
    if any(is_womens_competition(c) for c in (competition, competition_name) if c):
        return True
    return any(bool(_WOMENS_TEAM_RE.search(t or '')) for t in (team_a, team_b))


def pm_finish(listed: datetime | None, finished: datetime | None) -> datetime | None:
    """Polymarket's finishedTimestamp, or None when it is a placeholder.

    164 fixtures (J2 League, the women's Champions League...) carry a finish
    stamped at exactly the listed start, which is not a finish at all.
    """
    if finished is not None and listed is not None and \
            abs(finished - listed) < timedelta(minutes=1):
        return None
    return finished


def pm_end_bound(finished: datetime | None, closed: datetime | None) -> datetime | None:
    """The earliest moment known to be after a Polymarket match ended."""
    bounds = [t for t in (finished, closed) if t is not None]
    return min(bounds) if bounds else None


def link_fixture(team_a: str, team_b: str, ko: datetime | None, ev_date: date | None,
                 idx: TeamIndex, by_date: dict[date, list[tuple]],
                 end_hint: datetime | None = None,
                 end_window: timedelta = timedelta(hours=4),
                 date_tol_days: int = 1) -> tuple[str, dict | None]:
    """(status, link) for one fixture against `matches`.

    link = {match_id, swapped, score, db_ko, league}. Every ambiguity fails
    closed: two candidates scoring the same, or one reading equally well both
    ways round, link nothing.

    Where our row has a real time, it must sit within 3h of the listed start
    OR fit the end of the match -- the second is what recovers a rescheduled
    fixture whose listing kept the old date.
    """
    ca, cb = idx.candidates(team_a), idx.candidates(team_b)
    if ca is None or cb is None:
        return 'not_in_model', None
    if not ca or not cb:
        return 'no_candidate', None
    anchors = {x for x in (ko.date() if ko else None, ev_date,
                           end_hint.date() if end_hint else None) if x is not None}
    if not anchors:
        return 'no_candidate', None
    days = {a + timedelta(days=dd) for a in anchors
            for dd in range(-date_tol_days, date_tol_days + 1)}

    scored = []
    for day in sorted(days):
        for (mid, home, away, stored, league) in by_date.get(day, []):
            if not ({home, away} & ca) or not ({home, away} & cb):
                continue
            real = db_real_kickoff(stored, league)
            if real is not None and (ko is not None or end_hint is not None):
                near_listed = ko is not None and abs(real - ko) <= timedelta(hours=3)
                fits_end = end_hint is not None and \
                    MIN_MATCH <= end_hint - real <= end_window
                if not (near_listed or fits_end):
                    continue
            direct = min(idx.score(team_a, home), idx.score(team_b, away))
            swapped = min(idx.score(team_a, away), idx.score(team_b, home))
            if max(direct, swapped) < MIN_SIDE_SCORE:
                continue
            if direct == swapped:
                return 'ambiguous', None
            s, sw = (direct, False) if direct > swapped else (swapped, True)
            scored.append((s, mid, sw, real, league))
    if not scored:
        return 'no_candidate', None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1] != scored[1][1]:
        return 'ambiguous', None
    s, mid, sw, real, league = scored[0]
    return 'linked', {'match_id': mid, 'swapped': sw, 'score': round(s, 3),
                      'db_ko': real, 'league': league}


def _subject_side(subject_team: str | None, swapped: bool) -> str | None:
    if subject_team == 'draw':
        return 'draw'
    if subject_team not in ('a', 'b'):
        return None
    home_is_a = not swapped
    return 'home' if (subject_team == 'a') == home_is_a else 'away'


def _load_link_inputs(conn, include_learned: bool = True
                      ) -> tuple[TeamIndex, dict[date, list[tuple]]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name FROM teams")
        canonical = {tid: n for tid, n in cur.fetchall()}
        names: dict[int, list[str]] = defaultdict(list)
        for tid, n in canonical.items():
            names[tid].append(n)
        cur.execute("SELECT team_id, alias FROM team_aliases WHERE source NOT LIKE 'nba%%'")
        for tid, a in cur.fetchall():
            if a and a not in names[tid]:
                names[tid].append(a)
        cur.execute("""
            SELECT m.id, m.home_team_id, m.away_team_id, m.kickoff_utc, l.code
            FROM matches m
            JOIN seasons s ON s.id = m.season_id
            JOIN leagues l ON l.id = s.league_id
            WHERE m.kickoff_utc >= %s AND l.code <> 'USA-NBA'
        """, (datetime(2024, 7, 1, tzinfo=timezone.utc),))
        by_date: dict[date, list[tuple]] = defaultdict(list)
        for mid, h, a, ko, code in cur.fetchall():
            by_date[ko.date()].append((mid, h, a, ko, code))
    log.info('link: %d teams, %d matches since 2024-07', len(canonical),
             sum(len(v) for v in by_date.values()))
    return TeamIndex(names, canonical, include_learned), by_date


def link(conn, relink: bool = False) -> None:
    idx, by_date = _load_link_inputs(conn)
    where = '' if relink else 'WHERE link_status IS NULL'
    with conn.cursor() as cur:
        # One fixture = one pairing on one date. Polymarket files a fixture's
        # families under sibling events; they share this key.
        cur.execute(f"""
            SELECT venue, team_a, team_b, event_date,
                   min(kickoff_listed),
                   max(finished_at),
                   max(closed_at) FILTER (WHERE family = 'moneyline'),
                   min(closed_at) FILTER (WHERE family = 'moneyline'),
                   array_agg(market_id), array_agg(subject_team),
                   max(competition), max(competition_name)
            FROM venue_market_history {where}
            GROUP BY venue, team_a, team_b, event_date
            ORDER BY venue DESC
        """)
        groups = cur.fetchall()
    log.info('link: %d fixtures to link', len(groups))

    # Kalshi borrows its start from the Polymarket listing of the same match,
    # SETTLED first -- same ET date, both clubs agreeing, a unique pairing.
    # Polymarket groups sort first, so this run's are added as they settle;
    # only an incremental run needs the ones settled before it.
    pm_by_et: dict[str, list[tuple]] = defaultdict(list)
    if not relink:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT team_a, team_b, kickoff_utc
                FROM venue_market_history
                WHERE venue = 'polymarket' AND kickoff_utc IS NOT NULL
                  AND link_status IS NOT NULL
            """)
            for a, b, ko in cur.fetchall():
                pm_by_et[venues.et_date(ko)].append((a, b, ko))

    status_n: Counter = Counter()
    ko_n: Counter = Counter()
    updates = []
    for (venue, a, b, ev_date, listed, finished, game_close, first_close, mids, subj,
         comp, comp_name) in groups:
        womens = is_womens_fixture(comp, comp_name, a, b)
        if venue == 'polymarket':
            # finishedTimestamp is a loose end (see PM_END_WINDOW), and one
            # stamped at exactly the listed start is a placeholder, not a
            # finish (164 fixtures: J2 League, the women's Champions League...).
            finished = pm_finish(listed, finished)
            end_hint, end_bound = None, pm_end_bound(finished, first_close)
            status, lk = ('womens', None) if womens else \
                link_fixture(a, b, listed, ev_date, idx, by_date, finished, PM_END_WINDOW)
        else:
            # The 1X2 legs close when a winner is declared: a tight end.
            end_hint, end_bound = game_close, None
            listed = None
            et = ev_date.strftime('%Y%m%d') if ev_date else None
            hits = {x[2] for x in pm_by_et.get(et, [])
                    if venues.same_club(a, x[0]) and venues.same_club(b, x[1])}
            if len(hits) == 1:
                listed = hits.pop()
            status, lk = ('womens', None) if womens else \
                link_fixture(a, b, listed, ev_date, idx, by_date, end_hint)
        ko, src = settle_kickoff(listed, lk['db_ko'] if lk else None, end_hint, end_bound)
        if venue == 'kalshi' and src in ('listed', 'listed_unchecked'):
            src = 'pm_fixture'
        status_n[(venue, status)] += 1
        ko_n[(venue, src)] += 1
        if venue == 'polymarket' and ko is not None:
            pm_by_et[venues.et_date(ko)].append((a, b, ko))
        for market_id, st in zip(mids, subj):
            updates.append((
                venue, market_id, status,
                lk['match_id'] if lk else None,
                lk['swapped'] if lk else None,
                _subject_side(st, lk['swapped']) if lk else None,
                lk['score'] if lk else None,
                ko, src,
            ))
    with conn.cursor() as cur:
        # A start that moved invalidates any price already read at the old one.
        psycopg2.extras.execute_values(cur, """
            UPDATE venue_market_history v SET
                link_status = u.status, match_id = u.match_id::int,
                match_swapped = u.swapped::boolean, subject_side = u.side,
                link_score = u.score::numeric, linked_at = now(),
                prices_status = CASE WHEN v.kickoff_utc IS DISTINCT FROM u.ko::timestamptz
                                     THEN NULL ELSE v.prices_status END,
                prices_fetched_at = CASE WHEN v.kickoff_utc IS DISTINCT FROM u.ko::timestamptz
                                         THEN NULL ELSE v.prices_fetched_at END,
                kickoff_utc = u.ko::timestamptz,
                kickoff_source = u.ko_src,
                updated_at = now()
            FROM (VALUES %s) AS u(venue, market_id, status, match_id, swapped, side, score, ko, ko_src)
            WHERE v.venue = u.venue AND v.market_id = u.market_id
        """, updates, page_size=1000)
    for (venue, status), n in sorted(status_n.items()):
        log.info('link: %-10s %-13s %6d fixtures', venue, status, n)
    for (venue, src), n in sorted(ko_n.items(), key=lambda x: (x[0][0], str(x[0][1]))):
        log.info('kick-off: %-10s %-13s %6d fixtures', venue, str(src), n)


ALIAS_TIME_TOL = timedelta(minutes=15)
ALIAS_MIN_EVIDENCE = 3


def alias_proposals(venue: str, a: str, b: str, ev_date: date | None,
                    listed: datetime | None, finished: datetime | None,
                    game_close: datetime | None, idx: TeamIndex,
                    by_date: dict[date, list[tuple]]) -> set[tuple[str, int]]:
    """{(venue name, our team id)} one unlinked fixture proposes. See learn_aliases."""
    end_hint = finished if venue == 'polymarket' else game_close
    ko = listed if venue == 'polymarket' else None
    anchors = {x for x in (ko.date() if ko else None, ev_date,
                           end_hint.date() if end_hint else None) if x}
    proposals: set[tuple[str, int]] = set()
    for day in {d + timedelta(days=k) for d in anchors for k in (-1, 0, 1)}:
        for (mid, home, away, stored, league) in by_date.get(day, []):
            real = db_real_kickoff(stored, league)
            if real is None:
                continue
            on_time = (ko is not None and abs(real - ko) <= ALIAS_TIME_TOL) or \
                (end_hint is not None and
                 MIN_MATCH <= end_hint - real <= timedelta(minutes=150))
            if not on_time:
                continue
            for anchor, other, anchor_tid, other_tid in (
                    (a, b, home, away), (b, a, away, home),
                    (a, b, away, home), (b, a, home, away)):
                if idx.score(anchor, anchor_tid) >= 1.0 and \
                        idx.score(other, other_tid) < MIN_SIDE_SCORE:
                    proposals.add((other, other_tid))
    return proposals


def learn_aliases(conn, min_evidence: int = ALIAS_MIN_EVIDENCE) -> dict[str, str]:
    """Spellings our teams table does not know, learned from the kick-off clock.

    Football-Data writes "Espanol", "M'gladbach", "Buyuksehyr"; no token rule
    reaches them from "RCD Espanyol de Barcelona", "Borussia Mönchengladbach",
    "Rams Başakşehir FK". But a club plays one match at a time. When one side
    of an unlinked fixture is certainly club X (score 1.0), and our only row for
    X kicks off within 15 minutes of the venue's start (or fits the match's
    end), the other side IS X's opponent in that row. One such fixture is a
    coincidence; the same name landing on the same club twice, and never on a
    different one, is a spelling. Only Stage A leagues take part, because only
    their times are times.

    Writes venue_team_aliases.json with the evidence count of each entry, and
    returns what it wrote. Nothing here links anything; --relink does.
    """
    idx, by_date = _load_link_inputs(conn, include_learned=False)
    leagues_of: dict[int, set[str]] = defaultdict(set)
    for rows in by_date.values():
        for (_mid, home, away, _ko, league) in rows:
            leagues_of[home].add(league)
            leagues_of[away].add(league)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT venue, team_a, team_b, event_date,
                   min(kickoff_listed), max(finished_at),
                   max(closed_at) FILTER (WHERE family = 'moneyline'),
                   max(competition), max(competition_name)
            FROM venue_market_history
            WHERE link_status = 'no_candidate'
            GROUP BY venue, team_a, team_b, event_date
        """)
        groups = cur.fetchall()
    canonical = {tid: ns[0] for tid, ns in idx.names.items()}
    votes: dict[str, Counter] = defaultdict(Counter)
    for venue, a, b, ev_date, listed, finished, game_close, comp, comp_name in groups:
        if is_womens_fixture(comp, comp_name, a, b):
            continue
        proposals = alias_proposals(venue, a, b, ev_date, listed,
                                    pm_finish(listed, finished), game_close, idx, by_date)
        # One fixture, one reading: two different opponents on time is not evidence.
        if len(proposals) == 1:
            name, tid = proposals.pop()
            votes[name][tid] += 1

    learned: dict[str, str] = {}
    evidence: dict[str, dict] = {}
    refused: dict[str, str] = {}
    for name, c in votes.items():
        (tid, n), total = c.most_common(1)[0], sum(c.values())
        if n < min_evidence or n != total:
            continue
        # A name that IS another club of the same league ("Crystal Palace",
        # paired with Man United by a women's fixture on the men's clock) is
        # not a spelling of this one. Only an exact name in a shared league
        # counts: "RCD Espanyol de Barcelona" contains Barcelona and is still
        # Espanyol, and Vitória SC is not Brazil's Vitoria.
        key = _norm_key(name)
        rivals = [t for t in idx.canonical_to_ids.get(key, set())
                  if t != tid and leagues_of.get(t, set()) & leagues_of.get(tid, set())]
        if rivals:
            refused[name] = f'{canonical[tid]} (it is the name of {canonical[rivals[0]]})'
            continue
        learned[name] = canonical[tid]
        evidence[name] = {'team': canonical[tid], 'fixtures': n}
    payload = {
        '_about': ('Venue spellings of clubs in our teams table, learned by '
                   'stage_j_venue_history.py --learn-aliases from kick-off-time '
                   'co-occurrence (one side certain, our row within 15 min, the same '
                   'club every time, at least %d fixtures). Hand-curated '
                   'agent/team_aliases.json wins over this file.') % min_evidence,
        'aliases': dict(sorted(learned.items())),
        'evidence': dict(sorted(evidence.items())),
    }
    LEARNED_ALIASES_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + '\n')
    log.info('learned %d aliases (%d names had some evidence) -> %s',
             len(learned), len(votes), LEARNED_ALIASES_PATH.name)
    for name in sorted(learned):
        log.info('  %-40s -> %-22s (%d fixtures)', name, learned[name], evidence[name]['fixtures'])
    for name in sorted(refused):
        log.info('  refused %-32s -> %s', name, refused[name])
    return learned


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def pm_price_row(r: dict) -> dict:
    ko = int(r['kickoff_utc'].timestamp())
    hist = get_json(f'{CLOB}/prices-history',
                    {'market': r['token0'], 'startTs': ko - HISTORY_SPAN_S, 'endTs': ko,
                     'fidelity': 5}, CLOB_PACER) or {}
    pts = [(int(p['t']), _num(p.get('p'))) for p in hist.get('history') or []
           if isinstance(p.get('t'), (int, float))]
    out = horizon_prices(pts, ko)
    out.update(bid_close=None, ask_close=None, buy0_price=None, buy0_at=None,
               buy1_price=None, buy1_at=None, trades_1h=None)
    vol = r['volume_usd']
    if vol is None or vol == 0 or vol >= MIN_VOLUME_FOR_TRADES:
        try:
            trades = get_json(f'{DATA_API}/trades',
                              {'market': r['market_id'], 'takerOnly': 'true', 'end': ko,
                               'limit': 200}, DATA_PACER) or []
            out.update(last_buys(trades if isinstance(trades, list) else [], ko))
        except RuntimeError as e:
            # The mid is still good; the paid price stays unknown (NULL), which
            # is not the same claim as "nobody bought".
            log.debug('trades %s: %s', r['market_id'], e)
    out['prices_status'] = 'ok' if pts else 'no_history'
    return out


def _kx_candles(ticker: str, series: str, historical: bool, start: int, end: int,
                interval: int) -> list[dict]:
    if historical:
        path = f'{KALSHI}/historical/markets/{ticker}/candlesticks'
    else:
        path = f'{KALSHI}/series/{series}/markets/{ticker}/candlesticks'
    d = get_json(path, {'start_ts': start, 'end_ts': end, 'period_interval': interval},
                 KALSHI_PACER)
    return (d or {}).get('candlesticks') or []


def kalshi_price_row(r: dict, cutoff: datetime | None) -> dict:
    """One call: the minute candles of the last 65 minutes before kick-off.

    That carries the close book and the 1h mid. The 24h / 6h horizons would
    need a second, hourly call per market, and Kalshi's public tier refuses
    anything much past ~6 requests a second -- so they are left NULL on
    Kalshi rather than doubling a multi-hour backfill.
    """
    ko = int(r['kickoff_utc'].timestamp())
    # The series ticker is the market ticker's first segment.
    series = r['market_id'].split('-')[0]
    historical = cutoff is not None and r['kickoff_utc'] < cutoff - timedelta(days=1)
    minute = _kx_candles(r['market_id'], series, historical, ko - 65 * 60, ko, 1)
    if not minute and not historical and cutoff is not None:
        # Settled near the cutoff: try the other tier before calling it empty.
        minute = _kx_candles(r['market_id'], series, True, ko - 65 * 60, ko, 1)
    out = kalshi_prices(minute, minute, ko)
    out['mid_24h'] = out['mid_6h'] = None
    out.update(buy0_price=None, buy0_at=None, buy1_price=None, buy1_at=None, trades_1h=None)
    out['prices_status'] = 'ok' if minute else 'no_history'
    return out


PRICE_COLS = ['mid_24h', 'mid_6h', 'mid_1h', 'mid_close', 'close_at', 'bid_close',
              'ask_close', 'buy0_price', 'buy0_at', 'buy1_price', 'buy1_at', 'trades_1h',
              'prices_status']


def _write_prices(conn, results: list[tuple[str, str, dict]]) -> None:
    if not results:
        return
    vals = [(v, mid, *[res.get(c) for c in PRICE_COLS]) for v, mid, res in results]
    sets = ', '.join(f'{c} = u.{c}::{t}' for c, t in zip(PRICE_COLS, [
        'numeric', 'numeric', 'numeric', 'numeric', 'timestamptz', 'numeric', 'numeric',
        'numeric', 'timestamptz', 'numeric', 'timestamptz', 'int', 'text']))
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, f"""
            UPDATE venue_market_history v SET {sets},
                   prices_fetched_at = now(), updated_at = now()
            FROM (VALUES %s) AS u(venue, market_id, {', '.join(PRICE_COLS)})
            WHERE v.venue = u.venue AND v.market_id = u.market_id
        """, vals, page_size=500)


def prices(conn, limit: int | None, workers: int, venue: str | None,
           linked_only: bool) -> None:
    # Rows nobody traded are closed without a call -- on Kalshi only, whose
    # volume is always reported. Polymarket's is missing for whole months.
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE venue_market_history SET prices_status = 'no_volume', prices_fetched_at = now()
            WHERE prices_status IS NULL AND venue = 'kalshi' AND COALESCE(volume_usd, 0) < %s
        """, (MIN_VOLUME_FOR_PRICES,))
        log.info('prices: %d untraded rows closed with no call', cur.rowcount)
        cur.execute("""
            UPDATE venue_market_history SET prices_status = 'no_kickoff', prices_fetched_at = now()
            WHERE prices_status IS NULL AND kickoff_utc IS NULL AND link_status IS NOT NULL
        """)
        log.info('prices: %d rows with no kick-off to read a close at', cur.rowcount)

    cutoff = None
    try:
        c = get_json(f'{KALSHI}/historical/cutoff', {}, KALSHI_PACER) or {}
        cutoff = parse_ts(c.get('market_settled_ts'))
    except RuntimeError as e:
        log.warning('kalshi cutoff unknown (%s): every market will try both tiers', e)

    # 'error' is a failed request, not an answer: it is asked again.
    conds = ["(v.prices_status IS NULL OR v.prices_status = 'error')",
             "v.kickoff_utc IS NOT NULL", "v.kickoff_utc < now() - %s::interval"]
    args: list = [f'{int(SETTLED_AFTER.total_seconds())} seconds']
    if venue:
        conds.append('v.venue = %s')
        args.append(venue)
    if linked_only:
        conds.append('v.match_id IS NOT NULL')
    # What the Lab can read first: games in bt_lab_matches, the families it
    # tests (1X2, over/under 2.5, BTTS), spreads last.
    sql = f"""
        SELECT v.venue, v.market_id, v.token0, v.kickoff_utc, v.volume_usd, v.family
        FROM venue_market_history v
        LEFT JOIN bt_lab_matches b ON b.match_id = v.match_id
        WHERE {' AND '.join(conds)}
        ORDER BY (v.match_id IS NULL), (b.match_id IS NULL),
                 NOT (v.family IN ('moneyline', 'btts') OR (v.family = 'totals' AND v.line = 2.5)),
                 (v.family = 'spreads'), v.kickoff_utc DESC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql + (f' LIMIT {int(limit)}' if limit else ''), args)
        todo = cur.fetchall()
    log.info('prices: %d rows to fetch (%s)', len(todo),
             ', '.join(f'{k} {v}' for k, v in Counter(r['venue'] for r in todo).items()))

    def work(r):
        try:
            if r['venue'] == 'polymarket':
                return r['venue'], r['market_id'], pm_price_row(r)
            return r['venue'], r['market_id'], kalshi_price_row(r, cutoff)
        except Exception as e:      # noqa: BLE001 - one market is not the run
            log.debug('%s %s: %s', r['venue'], r['market_id'], e)
            return r['venue'], r['market_id'], {'prices_status': 'error'}

    t0 = time.time()
    done = 0
    batch: list = []
    status_n = Counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, todo):
            batch.append(res)
            status_n[res[2]['prices_status']] += 1
            if len(batch) >= 500:
                _write_prices(conn, batch)
                done += len(batch)
                batch = []
                rate = done / max(1e-9, time.time() - t0)
                log.info('prices: %d / %d  (%.1f/s, ~%.0f min left)  %s', done, len(todo),
                         rate, (len(todo) - done) / max(rate, 1e-9) / 60, dict(status_n))
    _write_prices(conn, batch)
    log.info('prices: done, %s', dict(status_n))


def refresh_lab(conn) -> None:
    """Rebuild what the Lab reads: bt_lab_matches (db/062, the matches and their
    sharp close) and bt_venue_prices (db/060, the exchanges on those matches)."""
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '300s'")
        cur.execute('SELECT refresh_bt_lab()')
        log.info('bt_lab_matches: %d matches', cur.fetchone()[0])
        cur.execute('SELECT refresh_bt_venue_prices()')
        n = cur.fetchone()[0]
        cur.execute("""
            SELECT venue, side, count(*), count(p_exec), count(p_mid)
            FROM bt_venue_prices GROUP BY 1, 2 ORDER BY 1, 2
        """)
        rows = cur.fetchall()
    log.info('bt_venue_prices: %d rows', n)
    for venue, side, total, n_exec, n_mid in rows:
        log.info('  %-11s %-6s %6d  (paid price %d, mid %d)', venue, side, total, n_exec, n_mid)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT venue, family, count(*),
                   count(*) FILTER (WHERE winner IS NOT NULL OR payout0 IS NOT NULL),
                   count(*) FILTER (WHERE match_id IS NOT NULL),
                   count(*) FILTER (WHERE prices_status = 'ok'),
                   count(*) FILTER (WHERE mid_close IS NOT NULL),
                   count(*) FILTER (WHERE buy0_price IS NOT NULL OR ask_close IS NOT NULL),
                   min(kickoff_utc)::date, max(kickoff_utc)::date
            FROM venue_market_history GROUP BY 1, 2 ORDER BY 1, 2
        """)
        print(f"\n{'venue':<11}{'family':<10}{'rows':>8}{'resolved':>9}{'linked':>8}"
              f"{'priced':>8}{'close':>8}{'paid':>8}  range")
        for v, f, n, res, lk, ok, cl, paid, lo, hi in cur.fetchall():
            print(f'{v:<11}{f:<10}{n:>8}{res:>9}{lk:>8}{ok:>8}{cl:>8}{paid:>8}  {lo} -> {hi}')

        cur.execute("""
            SELECT venue, link_status, count(DISTINCT (team_a, team_b, event_date))
            FROM venue_market_history GROUP BY 1, 2 ORDER BY 1, 2
        """)
        print('\nfixtures by link status')
        for v, s, n in cur.fetchall():
            print(f'  {v:<11}{str(s):<14}{n:>7}')

        cur.execute("""
            SELECT l.code, count(DISTINCT v.match_id) FILTER (WHERE v.venue = 'polymarket'),
                   count(DISTINCT v.match_id) FILTER (WHERE v.venue = 'kalshi'),
                   count(DISTINCT b.match_id)
            FROM venue_market_history v
            JOIN matches m ON m.id = v.match_id
            JOIN seasons s ON s.id = m.season_id JOIN leagues l ON l.id = s.league_id
            LEFT JOIN bt_lab_matches b ON b.match_id = v.match_id
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"\n{'league':<14}{'PM matches':>11}{'Kalshi':>8}{'in Lab':>8}")
        for code, pm, kx, lab in cur.fetchall():
            print(f'{code:<14}{pm:>11}{kx:>8}{lab:>8}')

        # The join, checked against the money: what the venue paid on a linked
        # market against what our own result says it should have. A wrong
        # match or a flipped side cannot hide here -- it disagrees on about
        # half of everything it touches.
        cur.execute("""
            SELECT venue, family, count(*), count(*) FILTER (WHERE agree)
            FROM (
              SELECT v.venue, v.family,
                     (v.payout0 = 1) = CASE
                       WHEN v.family = 'moneyline' THEN CASE v.subject_side
                            WHEN 'home' THEN m.home_score > m.away_score
                            WHEN 'away' THEN m.away_score > m.home_score
                            ELSE m.home_score = m.away_score END
                       WHEN v.family = 'totals' THEN m.home_score + m.away_score > v.line
                       WHEN v.family = 'btts' THEN m.home_score > 0 AND m.away_score > 0
                     END AS agree
              FROM venue_market_history v JOIN matches m ON m.id = v.match_id
              WHERE v.payout0 IN (0, 1) AND m.home_score IS NOT NULL AND v.family <> 'spreads'
            ) x GROUP BY 1, 2 ORDER BY 1, 2
        """)
        print('\nvenue payout vs our result, linked markets')
        for v, f, n, agree in cur.fetchall():
            print(f'  {v:<11}{f:<10}{agree:>7} / {n:<7} {100.0 * agree / max(n, 1):6.2f}%')

        cur.execute("""
            SELECT venue, kickoff_source, count(DISTINCT (team_a, team_b, event_date))
            FROM venue_market_history GROUP BY 1, 2 ORDER BY 1, 2
        """)
        print('\nkick-off source, fixtures')
        for v, src, n in cur.fetchall():
            print(f'  {v:<11}{str(src):<14}{n:>7}')

        # How far the listing sits from the real start (our London-read time),
        # and how long after the settled start the match ended.
        cur.execute("""
            SELECT venue,
                   percentile_cont(ARRAY[0.01, 0.5, 0.99]) WITHIN GROUP (ORDER BY
                     extract(epoch FROM kickoff_listed - kickoff_utc) / 60),
                   count(*) FILTER (WHERE abs(extract(epoch FROM kickoff_listed - kickoff_utc)) > 3600),
                   count(*)
            FROM (SELECT DISTINCT ON (venue, team_a, team_b, event_date) *
                  FROM venue_market_history
                  WHERE kickoff_source = 'match_london' AND kickoff_listed IS NOT NULL) x
            GROUP BY 1
        """)
        print('\nlisted start minus the real one (minutes: p1 / p50 / p99, >1h off)')
        for v, pct, off, n in cur.fetchall():
            print(f'  {v:<11}{pct[0]:+8.0f} {pct[1]:+6.0f} {pct[2]:+8.0f}   {off} of {n} fixtures')
        cur.execute("""
            SELECT venue,
                   percentile_cont(ARRAY[0.01, 0.1, 0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY
                     extract(epoch FROM COALESCE(finished_at, closed_at) - kickoff_utc) / 60),
                   count(*)
            FROM (SELECT DISTINCT ON (venue, team_a, team_b, event_date) *
                  FROM venue_market_history
                  WHERE kickoff_source = 'match_london' AND family = 'moneyline'
                    AND COALESCE(finished_at, closed_at) IS NOT NULL) x
            GROUP BY 1
        """)
        print('\nend of match minus the real start (minutes: p1 / p10 / p50 / p90 / p99)')
        for v, pct, n in cur.fetchall():
            print(f'  {v:<11}' + ' '.join(f'{x:6.0f}' for x in pct) + f'   n={n}')


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--pm-catalog', action='store_true')
    ap.add_argument('--kalshi-catalog', action='store_true')
    ap.add_argument('--link', action='store_true')
    ap.add_argument('--relink', action='store_true', help='re-link every row, not only new ones')
    ap.add_argument('--learn-aliases', action='store_true',
                    help='learn club spellings from unlinked fixtures (then --relink)')
    ap.add_argument('--prices', action='store_true')
    ap.add_argument('--refresh-lab', action='store_true',
                    help='rebuild what the Lab reads: bt_lab_matches (db/062) + bt_venue_prices (db/060)')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--start', type=date.fromisoformat, default=PM_START)
    ap.add_argument('--recent-days', type=int, default=None,
                    help='--pm-catalog from N days ago (the daily run), instead of --start')
    ap.add_argument('--end', type=date.fromisoformat,
                    default=(datetime.now(timezone.utc) - SETTLED_AFTER).date())
    ap.add_argument('--limit', type=int, default=None, help='--prices: at most N rows')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--venue', choices=['polymarket', 'kalshi'], default=None)
    ap.add_argument('--linked-only', action='store_true',
                    help='--prices: only rows joined to a match (what the Lab reads)')
    args = ap.parse_args()

    run_all = not any([args.pm_catalog, args.kalshi_catalog, args.link, args.relink,
                       args.learn_aliases, args.prices, args.refresh_lab, args.report])
    if args.recent_days is not None:
        args.start = args.end - timedelta(days=args.recent_days)
    conn = connect()
    if run_all or args.pm_catalog:
        pm_catalog(conn, args.start, args.end)
    if run_all or args.kalshi_catalog:
        kalshi_catalog(conn)
    if run_all or args.link or args.relink:
        link(conn, relink=args.relink)
    if args.learn_aliases:
        learn_aliases(conn)
    if run_all or args.prices:
        prices(conn, args.limit, args.workers, args.venue, args.linked_only)
    if run_all or args.prices or args.refresh_lab:
        refresh_lab(conn)
    if run_all or args.report:
        report(conn)


if __name__ == '__main__':
    main()
