#!/usr/bin/env python3
"""
Two exchanges, one price — the agent side.

The site got this first (site/app/lib/venues.ts). This is the same rule for
the Python agents, because "the best odds" answering differently in the
browser and in the trader is a bug waiting to happen, and because the point of
carrying both venues is worth nothing if the thing that actually bets only
looks at one of them.

WHAT IT DOES
    Given Polymarket's quote for a market and Kalshi's quote for the SAME
    market, say which one to buy at and what the difference is worth.

THE FEE, AND WHAT IT ACTUALLY CHANGES
    Polymarket charges the taker 0.05 x p x (1-p) per share and Kalshi
    0.07 x p x (1-p) per contract -- 40% more. At an even-money price that is
    1.25pp against 1.75pp.

    It does NOT flip which venue is cheaper. Searched exhaustively over every
    price and every gross gap at or above MIN_GAP, the largest net
    disadvantage a gross-cheaper Kalshi quote can carry is +0.000004 -- zero.
    That falls out of the arithmetic: the fee difference is 0.02 x p x (1-p),
    which maxes at 0.005 = MIN_GAP, so a gap big enough to call cannot be
    eaten by it. Any claim that netting the fee "stops Kalshi winning prices
    it should not" is wrong, and was removed from this file and the site on
    2026-09-20 rather than left to sound good.

    What it does change is worth having anyway:
      * THE SAVING. A one-cent gross advantage on Kalshi is worth about half
        a cent once the fee lands -- roughly half of a median saving.
      * THE TIE. Where the two print the SAME price near even money the fee
        difference reaches 0.5pp on its own, and Polymarket is genuinely the
        cheaper venue. A gross comparison calls that a draw.

    Both numbers are kept: `ask` is what the venue prints, `net` is what it
    costs, and the pick is made on `net`.

A BOOK HAS TO BE A BOOK
    A lone sell order at 0.99 behind an empty bid side is the cheapest quote
    on the card by arithmetic and is not a market. The 100-game review
    measured that class at -38pp (the 20pp+ spread bucket, 430 rows quoting an
    ask near 0.90 that resolved at 0.529). `grade()` is the gate and
    `best_of()` refuses anything it grades `none`.

    The tell is the SPREAD, not the depth: CA Mineiro v EC Vitoria quoted bid
    0.55 / ask 0.99 behind $30,117 of depth and traded at 0.56 two minutes
    later. Depth only ever downgrades clean to thin.

    And the grade is read off the LEG being bought, not off the fixture's
    match-result ladder. A 2c 1X2 vouching for a 34c Over 2.5 produced a
    27.3pp "saving" on the site's board on 2026-09-20.

KALSHI'S FOOTBALL
    `/events` takes ONE series_ticker at a time -- a comma list returns
    nothing, and there is no category or tag filter -- and football is spread
    across ~139 game series. It also rate-limits hard. Measured 2026-09-20:

        10 concurrent, no pacing   ->  112 of 139 refused with 429
         6 workers, 0.10s apart    ->  105 retries, 27.9s
         4 workers, 0.25s apart    ->    0 retries, 34.7s   <- what ships

    So the sweep is cached on disk and shared by every agent on this machine.
    Prices are re-read on their own clock through `/markets?tickers=`, which
    DOES take a batch.

Read-only. Kalshi's market data is public: no key, no account, nothing here
can place an order.

Usage:
    python venues.py --probe                  # sweep and report what Kalshi has
    python venues.py --fixture "Milan" "Lecce"
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from fixture_match import MIN_SIDE_SCORE, team_score

log = logging.getLogger(__name__)

POLYMARKET = 'polymarket'
KALSHI = 'kalshi'
VENUES = (POLYMARKET, KALSHI)

VENUE_NAME = {POLYMARKET: 'Polymarket', KALSHI: 'Kalshi'}

# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

FEE_RATE = {
    POLYMARKET: float(os.getenv('EDGE_FEE_RATE', '0.05')),
    KALSHI: float(os.getenv('KALSHI_FEE_RATE', '0.07')),
}


def taker_fee(price: float, venue: str) -> float:
    """Fee per $1 of payout. Both venues charge on p*(1-p)."""
    if price is None or not (0 < price < 1):
        return 0.0
    return FEE_RATE[venue] * price * (1 - price)


def net_cost(ask: float, venue: str) -> float:
    """What a $1 payout actually costs here, fee included."""
    return ask + taker_fee(ask, venue)


def kalshi_order_fee(shares: float, price: float) -> float:
    """Kalshi's real fee on ONE order: rounded UP to the cent.

    Not used by the comparison -- which is per $1 of payout -- but a small
    order pays a brutal effective rate because of this rounding, and any code
    that sizes a Kalshi order has to use it rather than the rate.
    """
    return math.ceil(FEE_RATE[KALSHI] * shares * price * (1 - price) * 100) / 100


# ---------------------------------------------------------------------------
# A quote, and how much of a market is behind it
# ---------------------------------------------------------------------------

CLEAN_SPREAD = 0.03
WIDE_SPREAD = 0.10
CLEAN_DEPTH_USD = 250.0

#: Outside this band the market is decided and the odds stop describing a bet
#: anyone would place, so neither venue "wins" it.
TRADEABLE = (0.02, 0.98)

#: Half a cent. Below it the two are one price -- Kalshi ticks in whole cents
#: and calling a sub-tick difference a better price is noise dressed as a
#: finding. Applied to the NET cost as well as the printed one, so the fee
#: difference has to be worth half a cent by itself before it decides a tie
#: (which happens only near even money, where it reaches 0.5pp).
MIN_GAP = 0.005


@dataclass(frozen=True)
class Quote:
    bid: float | None = None
    #: What buying this side costs now, per $1 of payout, BEFORE the fee.
    ask: float | None = None
    #: Dollars offered at the best ask, where the venue told us. None is "not
    #: known", never "nothing there".
    ask_depth_usd: float | None = None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return round(self.ask - self.bid, 4)

    @property
    def tradeable(self) -> bool:
        return self.ask is not None and TRADEABLE[0] < self.ask < TRADEABLE[1]


def quote_of(bid, ask, ask_depth_usd=None) -> Quote:
    b = float(bid) if bid is not None and 0 < float(bid) < 1 else None
    a = float(ask) if ask is not None and 0 < float(ask) < 1 else None
    return Quote(bid=b, ask=a, ask_depth_usd=(ask_depth_usd if a is not None else None))


EMPTY = Quote()


def grade(quotes) -> str:
    """'clean' | 'thin' | 'wide' | 'none', read off the WORST quote given.

    Pass ONE quote to grade the leg you are buying -- which is what the
    best-price decision runs on. Pass a whole ladder to describe a fixture.
    """
    qs = [q for q in quotes if q is not None]
    if not qs or any(q.spread is None for q in qs):
        return 'none'
    worst = max(q.spread for q in qs)
    if worst > WIDE_SPREAD + 1e-9:
        return 'none'
    if worst > CLEAN_SPREAD + 1e-9:
        return 'wide'
    if any(q.ask_depth_usd is not None and q.ask_depth_usd < CLEAN_DEPTH_USD for q in qs):
        return 'thin'
    return 'clean'


@dataclass(frozen=True)
class BestPick:
    """Where to buy one outcome, net of each venue's taker fee."""
    #: None when only one venue quotes it, when the two are level, or when the
    #: only cheaper book is not a real book.
    venue: str | None = None
    ask: float | None = None
    net: float | None = None
    #: How much the loser's NET cost is above the winner's, in probability
    #: points. Never the gross gap.
    saving_pp: float | None = None
    #: How many venues quoted a real, tradeable price. The pick means nothing
    #: at 1: there was nothing to be better than.
    quoted: int = 0

    @property
    def contested(self) -> bool:
        return self.quoted > 1


NO_PICK = BestPick()


def best_of(quotes: dict[str, Quote | None]) -> BestPick:
    """Which venue to buy at, given {venue: quote} for the SAME bet.

    Refuses rather than guesses: a venue with no real book cannot win, and two
    prices inside half a cent of each other are one price.
    """
    real = []
    for venue, q in quotes.items():
        if q is None or not q.tradeable or grade([q]) == 'none':
            continue
        real.append((venue, q.ask, net_cost(q.ask, venue)))
    if not real:
        return NO_PICK
    real.sort(key=lambda x: x[2])
    venue, ask, net = real[0]
    if len(real) == 1:
        return BestPick(venue=None, ask=ask, net=net, quoted=1)
    _, ask2, net2 = real[1]
    # Tie-break on the printed price too: a sub-tick net difference that
    # exists only because the fee curves cross is not a better price.
    if abs(ask - ask2) < MIN_GAP and abs(net - net2) < MIN_GAP:
        return BestPick(venue=None, ask=ask, net=net, quoted=len(real))
    return BestPick(
        venue=venue, ask=ask, net=net,
        saving_pp=round((net2 - net) * 100, 2), quoted=len(real),
    )


# ---------------------------------------------------------------------------
# Kalshi's football
# ---------------------------------------------------------------------------

KALSHI_API = os.getenv('KALSHI_API', 'https://api.elections.kalshi.com/trade-api/v2').rstrip('/')

#: Between the START of one request and the next. The measurement above is a
#: rate; four workers sharing a 0.25s gate is what produced the zero-refusal run.
GAP_S = float(os.getenv('KALSHI_GAP_S', '0.25'))
WORKERS = 4
RETRIES = 3
TIMEOUT_S = 15

#: `/markets?tickers=` takes a batch. This is what makes a fast price clock
#: affordable on top of a slow index.
QUOTE_BATCH = 100

CACHE_DIR = Path(os.getenv('KALSHI_CACHE_DIR', Path(__file__).parent / '.cache' / 'kalshi'))
#: The series list. A competition does not appear or vanish inside a day.
SERIES_TTL_S = 6 * 3600
#: The index: which fixture exists, under which tickers.
INDEX_TTL_S = 15 * 60
#: Prices move; the index does not.
QUOTE_TTL_S = 45

ET = ZoneInfo('America/New_York')

_MONTH = {'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
          'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'}

_slot_lock = threading.Lock()
_next_slot = [0.0]


def _paced_get(path: str, params: dict | None = None):
    """GET, paced and retried. 429 is transient here, not fatal."""
    last = None
    for attempt in range(RETRIES):
        with _slot_lock:
            now = time.monotonic()
            at = max(now, _next_slot[0])
            _next_slot[0] = at + GAP_S
        if at > now:
            time.sleep(at - now)
        try:
            r = requests.get(f'{KALSHI_API}{path}', params=params, timeout=TIMEOUT_S)
            if r.status_code == 429:
                time.sleep(0.5 * (attempt + 1))
                last = RuntimeError('kalshi 429')
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:      # noqa: BLE001 - one series failing is not the sweep
            last = e
            if attempt == RETRIES - 1:
                break
            time.sleep(0.5 * (attempt + 1))
    raise last if last else RuntimeError('kalshi unreachable')


def et_date(when) -> str | None:
    """The Eastern date a game is filed under: 20260920.

    ESPN, Kalshi and every US schedule file by this, and it is the strongest
    cheap discriminator for joining two feeds -- two different matches between
    the same two clubs on the same ET date do not happen. A 22:00Z kick-off
    and a 02:00Z one both land where the schedule puts them, which a UTC date
    would not.
    """
    if when is None:
        return None
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace('Z', '+00:00'))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(ET).strftime('%Y%m%d')


def kalshi_et_date(event_ticker: str) -> str | None:
    """`KXBRASILEIROCGAME-26SEP20VITCRU` -> `20260920`.

    THIS, not `occurrence_datetime`, is what the cross-venue join uses. The
    latter is the expected SETTLEMENT -- measured at kick-off + 3h on 55 of 67
    fixtures and +2h to +4.5h on the rest -- and reading it as a start time
    silently drops every competition whose games run longer.
    """
    m = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', event_ticker or '')
    if not m or m.group(2) not in _MONTH:
        return None
    return f'20{m.group(1)}{_MONTH[m.group(2)]}{m.group(3)}'


@dataclass
class KalshiFixture:
    event_ticker: str
    series: str
    competition: str
    #: As Kalshi writes them: "Home vs Away", verified against ESPN (Kalshi's
    #: own settlement source) on 3/3 fixtures 2026-07-22.
    home: str
    away: str
    et_date: str | None
    url: str
    #: 'home' | 'draw' | 'away' -> (ticker, label, Quote)
    legs: dict = field(default_factory=dict)
    #: "2.5" -> (ticker, label, Quote), each the OVER leg of the match total.
    totals: dict = field(default_factory=dict)
    #: "0.5" -> the same, for the FIRST HALF. Strategy 17's market.
    first_half: dict = field(default_factory=dict)
    volume: float | None = None

    def over(self, line: float, *, first_half: bool = False) -> Quote | None:
        book = self.first_half if first_half else self.totals
        leg = book.get(f'{line:.1f}')
        return leg[2] if leg else None

    def under(self, line: float, *, first_half: bool = False) -> Quote | None:
        """The NO leg of the same ticker.

        A real price, not a derived one: on a binary book, buying NO at
        1 - yes_bid IS selling YES at the bid. Its DEPTH is the size resting
        on the yes bid, which the event feed does not carry, so it is unknown.
        """
        q = self.over(line, first_half=first_half)
        if q is None:
            return None
        return Quote(
            bid=None if q.ask is None else round(1 - q.ask, 4),
            ask=None if q.bid is None else round(1 - q.bid, 4),
            ask_depth_usd=None,
        )

    def side(self, which: str) -> Quote | None:
        leg = self.legs.get(which)
        return leg[2] if leg else None


_DRAW_RE = re.compile(r'^(tie|draw)$', re.I)
_OVER_RE = re.compile(r'^over\b', re.I)


def _norm_tight(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _same_name(a: str, b: str) -> bool:
    """Two spellings inside Kalshi's OWN feed.

    Deliberately not the cross-venue scorer: here the sub-title is drawn from
    the same string the title is, so containment either way is the whole test.
    """
    x, y = _norm_tight(a), _norm_tight(b)
    return bool(x) and bool(y) and (x == y or x in y or y in x)


def split_vs(title: str) -> tuple[str, str] | None:
    m = re.match(r'^\s*(.+?)\s+vs\.?\s+(.+?)\s*$', title or '', re.I)
    if not m:
        return None
    home, away = m.group(1).strip(), m.group(2).strip()
    return (home, away) if home and away else None


def _leg_quote(m: dict) -> Quote:
    ask = _num(m.get('yes_ask_dollars'))
    size = _num(m.get('yes_ask_size_fp'))
    return quote_of(_num(m.get('yes_bid_dollars')), ask,
                    ask * size if ask is not None and size is not None else None)


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_game_event(ev: dict, series: str, competition: str) -> KalshiFixture | None:
    """One Kalshi 1X2 event -> one fixture, or nothing.

    Fails closed on every ambiguity. A side error on a football board does not
    blunt the reading, it inverts it: the home price shown against the away
    team is worse than no price at all.
    """
    teams = split_vs(str(ev.get('title') or ''))
    markets = [m for m in (ev.get('markets') or []) if (m.get('status') or 'active') != 'settled']
    if not teams or len(markets) < 2:
        return None
    home, away = teams

    legs: dict = {}
    for m in markets:
        label = str(m.get('yes_sub_title') or '').strip()
        if not label:
            return None
        if _DRAW_RE.match(label):
            side = 'draw'
        else:
            h, a = _same_name(label, home), _same_name(label, away)
            if h == a:
                return None
            side = 'home' if h else 'away'
        if side in legs:          # two markets claiming one side
            return None
        legs[side] = (m['ticker'], label, _leg_quote(m))

    if 'home' not in legs or 'away' not in legs:
        return None

    ticker = str(ev.get('event_ticker') or '')
    volume = sum(_num(m.get('volume_fp')) or 0.0 for m in markets)
    return KalshiFixture(
        event_ticker=ticker,
        series=series,
        competition=competition,
        home=home,
        away=away,
        et_date=kalshi_et_date(ticker),
        url=f'https://kalshi.com/markets/{series.lower()}/{ticker.lower()}',
        legs=legs,
        volume=volume or None,
    )


def parse_total_event(ev: dict) -> tuple[tuple[str, str], dict, float] | None:
    """"Home vs Away: Total Goals" -> the OVER leg at each line.

    Keyed on `floor_strike`, never on the sub-title's wording: the line is the
    market's own field and the sentence around it is prose that can change.
    """
    title = re.sub(r':\s*(First Half\s+)?Total(\s+Goals)?\s*$', '', str(ev.get('title') or ''), flags=re.I)
    teams = split_vs(title)
    if not teams:
        return None
    markets = [m for m in (ev.get('markets') or []) if (m.get('status') or 'active') != 'settled']
    out: dict = {}
    for m in markets:
        line = _num(m.get('floor_strike'))
        if line is None or not _OVER_RE.match(str(m.get('yes_sub_title') or '')):
            continue
        key = f'{line:.1f}'
        if key in out:            # two rungs on one line is a ladder we cannot read
            return None
        out[key] = (m['ticker'], str(m.get('yes_sub_title') or ''), _leg_quote(m))
    if not out:
        return None
    return teams, out, sum(_num(m.get('volume_fp')) or 0.0 for m in markets)


def _event_suffix(ticker: str) -> str:
    return '-'.join((ticker or '').split('-')[1:])


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

def _cache_read(name: str, ttl_s: float):
    p = CACHE_DIR / name
    try:
        if time.time() - p.stat().st_mtime < ttl_s:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return None


def _cache_write(name: str, payload) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / f'{name}.tmp'
        tmp.write_text(json.dumps(payload))
        tmp.replace(CACHE_DIR / name)
    except OSError as e:
        log.debug('kalshi cache write failed: %s', e)


def soccer_series(force: bool = False) -> dict:
    """{'game': [{ticker,title,stem}], 'totals': {stem: ticker}, 'first_half': {...}}

    Filtered on the `tags` array, never on a substring of the blob: a naive
    text match pulls in a Diana Ross NYE market carrying a stray 'Soccer' tag
    beside 'Music'.
    """
    if not force:
        hit = _cache_read('series.json', SERIES_TTL_S)
        if hit:
            return hit

    data = _paced_get('/series', {'category': 'Sports'})
    soccer = [s for s in (data.get('series') or [])
              if s.get('category') == 'Sports' and 'Soccer' in (s.get('tags') or []) and s.get('ticker')]

    game, totals, first_half = [], {}, {}
    for s in soccer:
        t = s['ticker']
        if t.endswith('GAME'):
            game.append({
                'ticker': t,
                'title': re.sub(r'\s+Game$', '', s.get('title') or t),
                'stem': t[2:-4] if t.startswith('KX') else t[:-4],
            })
        elif t.endswith('1HTOTAL'):
            first_half[t[2:-7] if t.startswith('KX') else t[:-7]] = t
        elif t.endswith('TOTAL') and not t.endswith('TEAMTOTAL'):
            # The match-goals ladder, and only that one. TEAMTOTAL is one
            # team's goals; neither is the number our tables are keyed to.
            totals[t[2:-5] if t.startswith('KX') else t[:-5]] = t

    game.sort(key=lambda s: s['ticker'])
    out = {'game': game, 'totals': totals, 'first_half': first_half}
    _cache_write('series.json', out)
    return out


def _events(series_ticker: str) -> list | None:
    """One series' open events, or None when Kalshi would not answer.

    None and an empty list are different claims and the caller counts them
    apart.
    """
    try:
        d = _paced_get('/events', {
            'series_ticker': series_ticker, 'status': 'open',
            'limit': 200, 'with_nested_markets': 'true',
        })
        return d.get('events') or []
    except Exception:             # noqa: BLE001 - one competition, not the sweep
        return None


def sweep_index(force: bool = False) -> dict:
    """Every soccer fixture Kalshi lists, with its 1X2 and goals ladders.

    ~34 seconds when it actually runs. Cached on disk for 15 minutes and
    shared by every agent on this machine.
    """
    if not force:
        hit = _cache_read('index.json', INDEX_TTL_S)
        if hit:
            return hit

    series = soccer_series()
    game = series['game']
    ok = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        pages = list(ex.map(lambda s: _events(s['ticker']), game))

    fixtures: list[KalshiFixture] = []
    active_stems: list[str] = []
    for s, evs in zip(game, pages):
        if evs is None:
            continue
        ok += 1
        parsed = [parse_game_event(ev, s['ticker'], s['title']) for ev in evs]
        parsed = [f for f in parsed if f]
        fixtures.extend(parsed)
        if parsed:
            active_stems.append(s['stem'])

    # The goals ladders are swept only for the competitions the 1X2 sweep just
    # found a fixture in. Kalshi has ~138 soccer TOTAL series against ~139 GAME
    # ones, so asking for all of them would double a 34-second sweep to buy
    # nothing -- about 25 competitions are actually playing.
    by_suffix = {_event_suffix(f.event_ticker): f for f in fixtures}
    for family, attr in (('totals', 'totals'), ('first_half', 'first_half')):
        tickers = [series[family][stem] for stem in active_stems if stem in series[family]]
        if not tickers:
            continue
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            ladders = list(ex.map(_events, tickers))
        for evs in ladders:
            for ev in evs or []:
                parsed = parse_total_event(ev)
                if not parsed:
                    continue
                teams, legs, volume = parsed
                # The 1X2 and its ladders share an event ticker but for the
                # family: KXMLSGAME-26SEP20MIASD <-> KXMLSTOTAL-26SEP20MIASD.
                # An exact key -- and the teams are checked anyway, because an
                # exact key that is wrong is the worst kind.
                hit = by_suffix.get(_event_suffix(str(ev.get('event_ticker') or '')))
                if hit and _same_name(hit.home, teams[0]) and _same_name(hit.away, teams[1]):
                    setattr(hit, attr, legs)
                    hit.volume = (hit.volume or 0.0) + volume

    payload = {
        'fixtures': [_fixture_to_json(f) for f in fixtures],
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'series': len(game),
        'series_ok': ok,
    }
    _cache_write('index.json', payload)
    return payload


def _fixture_to_json(f: KalshiFixture) -> dict:
    def legs(d):
        return {k: [t, lbl, [q.bid, q.ask, q.ask_depth_usd]] for k, (t, lbl, q) in d.items()}
    return {
        'event_ticker': f.event_ticker, 'series': f.series, 'competition': f.competition,
        'home': f.home, 'away': f.away, 'et_date': f.et_date, 'url': f.url,
        'legs': legs(f.legs), 'totals': legs(f.totals), 'first_half': legs(f.first_half),
        'volume': f.volume,
    }


def _fixture_from_json(d: dict) -> KalshiFixture:
    def legs(x):
        return {k: (t, lbl, Quote(bid=q[0], ask=q[1], ask_depth_usd=q[2]))
                for k, (t, lbl, q) in (x or {}).items()}
    return KalshiFixture(
        event_ticker=d['event_ticker'], series=d['series'], competition=d['competition'],
        home=d['home'], away=d['away'], et_date=d.get('et_date'), url=d['url'],
        legs=legs(d.get('legs')), totals=legs(d.get('totals')),
        first_half=legs(d.get('first_half')), volume=d.get('volume'),
    )


def refresh_quotes(fixtures: list[KalshiFixture]) -> int:
    """Re-read every leg from `/markets?tickers=`.

    The index may be fifteen minutes old; its prices must not be. About three
    requests for a whole board.
    """
    tickers, where = [], {}
    for f in fixtures:
        for book in (f.legs, f.totals, f.first_half):
            for key, (ticker, label, _q) in book.items():
                tickers.append(ticker)
                where.setdefault(ticker, []).append((book, key, label))
    if not tickers:
        return 0

    fresh = {}
    uniq = sorted(set(tickers))
    for i in range(0, len(uniq), QUOTE_BATCH):
        batch = uniq[i:i + QUOTE_BATCH]
        try:
            d = _paced_get('/markets', {'tickers': ','.join(batch), 'limit': QUOTE_BATCH})
        except Exception:         # noqa: BLE001 - keep the index's own quotes
            continue
        for m in d.get('markets') or []:
            fresh[m['ticker']] = _leg_quote(m)

    n = 0
    for ticker, q in fresh.items():
        for book, key, label in where.get(ticker, []):
            book[key] = (ticker, label, q)
            n += 1
    return n


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

class KalshiSoccerIndex:
    """Kalshi's football, swept once and shared.

    Hold one of these per process. `fixture()` does the cross-venue join.

    ⚠️ NOTHING HERE MAY BLOCK A POLL LOOP. The in-play agents run a 60-second
       cycle against a live match; a 34-second sweep inside one would cost the
       minute it was measuring. So `ensure_fresh()` refreshes on a BACKGROUND
       thread and returns immediately, and `fixture()` answers from whatever
       snapshot is currently loaded -- which on the first cycles of a cold
       process is none at all.

       That is the right failure: no Kalshi quote means the trade books on
       Polymarket, exactly as it did before this module existed. A missing
       comparison is not a wrong one.
    """

    def __init__(self, *, auto_refresh: bool = True, background: bool = False):
        self._fixtures: list[KalshiFixture] = []
        self._loaded_at = 0.0
        self._priced_at = 0.0
        self._auto = auto_refresh
        self._background = background
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.series = 0
        self.series_ok = 0
        self.last_error: str | None = None

    def load(self, force: bool = False) -> int:
        if self._fixtures and not force and time.time() - self._loaded_at < INDEX_TTL_S:
            return len(self._fixtures)
        payload = sweep_index(force=force)
        self._fixtures = [_fixture_from_json(d) for d in payload['fixtures']]
        self._loaded_at = time.time()
        self._priced_at = 0.0
        self.series = payload.get('series', 0)
        self.series_ok = payload.get('series_ok', 0)
        return len(self._fixtures)

    def reprice(self, force: bool = False) -> int:
        if not self._fixtures:
            return 0
        if not force and time.time() - self._priced_at < QUOTE_TTL_S:
            return 0
        n = refresh_quotes(self._fixtures)
        self._priced_at = time.time()
        return n

    def ensure_fresh(self) -> bool:
        """Kick off a refresh if one is due. Never blocks; returns True when a
        worker is running."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return True
            stale_index = time.time() - self._loaded_at >= INDEX_TTL_S
            stale_price = self._fixtures and time.time() - self._priced_at >= QUOTE_TTL_S
            if not stale_index and not stale_price:
                return False
            self._worker = threading.Thread(
                target=self._refresh, name='kalshi-index', daemon=True)
            self._worker.start()
            return True

    def _refresh(self) -> None:
        try:
            self.load()
            self.reprice()
            self.last_error = None
        except Exception as e:      # noqa: BLE001 - a failed sweep is not an outage
            self.last_error = repr(e)
            log.warning('kalshi index refresh failed: %s', e)

    @property
    def ready(self) -> bool:
        return bool(self._fixtures)

    @property
    def age_s(self) -> float | None:
        return None if not self._loaded_at else time.time() - self._loaded_at

    @property
    def fixtures(self) -> list[KalshiFixture]:
        return self._fixtures

    def fixture(self, home: str, away: str, kickoff=None) -> KalshiFixture | None:
        """Kalshi's side of one fixture, or None.

        The join everything else leans on, and the one this project has been
        bitten by most often. It needs:

          * the ET DATE to agree -- far stronger than any name scorer, free,
            and checked first. Kalshi publishes no kick-off for football;
            `occurrence_datetime` is the expected settlement.
          * both names to clear the alias-aware scorer, WHOLE, never as
            substrings. "Real Salt Lake" and "Real Monarchs" share a token.
          * the CROSSED orientation to score worse. A pairing that works both
            ways is not a pairing, and on a derby that is exactly the case
            that would invert the reading.
          * the result to be unique. Two candidates is not a match.
        """
        if self._background:
            self.ensure_fresh()
        elif self._auto:
            self.load()
            self.reprice()
        day = et_date(kickoff)
        if day is None:
            return None

        hits = []
        for f in self._fixtures:
            if f.et_date != day:
                continue
            hh = team_score(home, f.home)
            aa = team_score(away, f.away)
            if hh < MIN_SIDE_SCORE or aa < MIN_SIDE_SCORE:
                continue
            if min(hh, aa) <= max(team_score(home, f.away), team_score(away, f.home)):
                continue
            hits.append(f)
        return hits[0] if len(hits) == 1 else None


#: One index per process, built lazily. Agents share it rather than each
#: paying for a 34-second sweep, and the disk cache shares it between them.
_shared: KalshiSoccerIndex | None = None
_shared_lock = threading.Lock()


def shared_index(*, background: bool = False) -> KalshiSoccerIndex:
    """The process-wide index.

    `background=True` is what a poll loop wants: the first call starts a sweep
    on its own thread and returns an empty index, and the agent carries on
    with Polymarket alone until it fills. Pass it from a daemon; leave it off
    in a script that can afford to wait.
    """
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = KalshiSoccerIndex(background=background)
        elif background and not _shared._background:      # noqa: SLF001
            _shared._background = True                    # noqa: SLF001
        return _shared


# ---------------------------------------------------------------------------
# What an agent actually calls
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Execution:
    """Where a paper trade should be booked, and what the alternative was."""
    venue: str
    ask: float
    net: float
    #: The other exchange's ask for the same bet, when it quoted one.
    alt_venue: str | None = None
    alt_ask: float | None = None
    #: How much the alternative would have cost above this one, in points.
    saving_pp: float | None = None

    @property
    def contested(self) -> bool:
        return self.alt_ask is not None


def choose(pm: Quote | None, kalshi: Quote | None) -> Execution | None:
    """Buy at the cheaper exchange, net of each one's taker fee.

    Returns None when neither venue has a real, tradeable book -- which is a
    refusal to trade, not a price of zero.
    """
    pick = best_of({POLYMARKET: pm, KALSHI: kalshi})
    if pick.ask is None:
        return None
    # `best_of` leaves `venue` None when there is nothing to be better than or
    # the two are level. An execution still has to happen somewhere, and level
    # means either -- so Polymarket, where every one of these strategies has
    # its history.
    venue = pick.venue
    if venue is None:
        pm_real = pm is not None and pm.tradeable and grade([pm]) != 'none'
        venue = POLYMARKET if pm_real else KALSHI
        ask = pm.ask if venue == POLYMARKET else kalshi.ask
        net = net_cost(ask, venue)
    else:
        ask, net = pick.ask, pick.net

    other = KALSHI if venue == POLYMARKET else POLYMARKET
    other_q = kalshi if venue == POLYMARKET else pm
    # The alternative has to clear the same gate the winner did. A one-sided
    # book quoted at 0.99 is not a price we were choosing against, and
    # recording it as one would make every such row look contested.
    alt_ask = (other_q.ask if other_q is not None and other_q.tradeable
               and grade([other_q]) != 'none' else None)
    return Execution(
        venue=venue, ask=ask, net=net,
        alt_venue=other if alt_ask is not None else None,
        alt_ask=alt_ask,
        saving_pp=pick.saving_pp,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _probe(args) -> None:
    idx = KalshiSoccerIndex(auto_refresh=False)
    t0 = time.time()
    n = idx.load(force=args.force)
    print(f'{n} fixtures from {idx.series_ok}/{idx.series} series in {time.time() - t0:.1f}s')
    idx.reprice(force=True)

    with_totals = sum(1 for f in idx.fixtures if f.totals)
    with_fh = sum(1 for f in idx.fixtures if f.first_half)
    clean = sum(1 for f in idx.fixtures
                if grade([f.side('home'), f.side('draw'), f.side('away')]) in ('clean', 'thin'))
    print(f'  {with_totals} with a goals ladder, {with_fh} with a first-half ladder, '
          f'{clean} with a real 1X2 book')
    for f in sorted(idx.fixtures, key=lambda x: -(x.volume or 0))[:10]:
        h, d, a = f.side('home'), f.side('draw'), f.side('away')
        o25 = f.over(2.5)
        print(f'  {f.competition[:20]:20s} {f.home[:20]:20s} v {f.away[:20]:20s} '
              f'{f.et_date} 1X2 {h.ask}/{d.ask if d else None}/{a.ask} '
              f'O2.5 {o25.ask if o25 else None} vol {f.volume:.0f}' if h and a else '')


def _fixture(args) -> None:
    idx = shared_index()
    f = idx.fixture(args.home, args.away, args.kickoff or datetime.now(timezone.utc))
    if not f:
        print('no Kalshi fixture matched')
        return
    print(f'{f.event_ticker}  {f.home} vs {f.away}  ({f.competition}, {f.et_date})')
    for side in ('home', 'draw', 'away'):
        q = f.side(side)
        print(f'  {side:5s} {q.bid}/{q.ask} grade {grade([q])}' if q else f'  {side:5s} -')
    for line, (_t, label, q) in sorted(f.totals.items()):
        print(f'  O{line:>4s} {q.bid}/{q.ask} grade {grade([q])}   {label}')
    for line, (_t, label, q) in sorted(f.first_half.items()):
        print(f'  1H O{line:>4s} {q.bid}/{q.ask} grade {grade([q])}   {label}')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--probe', action='store_true', help='sweep and report')
    ap.add_argument('--force', action='store_true', help='ignore the disk cache')
    ap.add_argument('--fixture', nargs=2, metavar=('HOME', 'AWAY'))
    ap.add_argument('--kickoff', default=None, help='ISO timestamp; defaults to now')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    if args.fixture:
        args.home, args.away = args.fixture
        _fixture(args)
    else:
        _probe(args)


if __name__ == '__main__':
    main()
