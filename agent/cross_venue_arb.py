#!/usr/bin/env python3
"""
Cross-venue arbitrage detector: Polymarket vs Kalshi.

Model-free. This does not use DC, the sim, or any fair-value estimate — it
only asks whether a set of legs that together guarantee exactly $1 of payout
can be bought for less than $1 after fees. If yes, the profit does not depend
on predicting anything.

Two arb shapes, both reduced to the same primitive (a basket of legs whose
combined payout is exactly 1 unit per share):

  DUTCH  buy YES on home + draw + away, each at whichever venue is cheapest.
         Exactly one wins. Cost = sum of three asks.

  PAIR   buy YES on venue A + NO on the same outcome at venue B.
         Exactly one wins. Cost = ask_yes_A + ask_no_B.

WHY THIS IS SOUND HERE — settlement rules match. Verified 2026-07-22:
  Polymarket: "refers only to the outcome within the first 90 minutes of
              regular play plus stoppage time"
  Kalshi:     "after 90 minutes plus stoppage time (does not include extra
              time or penalties)"
Both are regulation time. If either venue ever changes this, a cup tie going
to extra time turns a "guaranteed" basket into a guaranteed loss — RE-CHECK
`--audit-rules` before trusting any signal from this module.

BOOK CONVENTIONS (both easy to invert, both verified against live data):
  Polymarket /book → {"bids":[{price,size}], "asks":[{price,size}]}.
    Buying YES crosses `asks`. The NO side is a separate token with its own book.
  Kalshi /orderbook → {"orderbook_fp": {"yes_dollars":[[p,sz]], "no_dollars":[...]}}
    Both arrays are RESTING BIDS, not asks. To buy YES you cross the NO bids:
    yes_ask = 1 - best_no_bid. Verified 3/3 against the market payload's
    yes_ask_dollars on 2026-07-22.

FEES — the whole game. Both venues charge takers on p(1-p), which peaks at
p=0.50, exactly where a 1X2 draw leg tends to sit.
  Polymarket: fee = shares x 0.05 x p x (1-p)          (agent.edge_engine)
  Kalshi:     fee = ceil(0.07 x shares x p x (1-p))     rounded UP to the cent,
              per order — so small orders pay a brutal effective rate.
Sizing therefore cannot be "top of book": the fee is computed at the sized
quantity, and the ladder VWAP worsens with size. This module walks both.

Read-only. Reports opportunities; places nothing.

Usage:
    python cross_venue_arb.py                      # scan, print report
    python cross_venue_arb.py --days 3             # wider fixture window
    python cross_venue_arb.py --min-profit-pp 0.5  # only show >=0.5pp net
    python cross_venue_arb.py --gross              # ignore fees (upper bound)
    python cross_venue_arb.py --audit-rules        # re-verify settlement text
    python cross_venue_arb.py --json out.json      # machine-readable dump
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import math
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / 'ingest'))

load_dotenv(Path(__file__).parent.parent / 'ingest' / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('cross_venue_arb')

GAMMA_API = os.getenv('POLYMARKET_GAMMA_API', 'https://gamma-api.polymarket.com').rstrip('/')
CLOB_API = os.getenv('POLYMARKET_CLOB_API', 'https://clob.polymarket.com').rstrip('/')
KALSHI_API = os.getenv('KALSHI_API', 'https://api.elections.kalshi.com/trade-api/v2').rstrip('/')

PM_FEE_RATE = float(os.getenv('EDGE_FEE_RATE', '0.05'))
KALSHI_FEE_RATE = float(os.getenv('KALSHI_FEE_RATE', '0.07'))

# A fuzzy name match below this ratio is not trusted even with a date match.
FUZZY_CUTOFF = 0.80
# Kickoff times must agree within this to accept a join. Guards against the
# several distinct Botafogos / Nacionals that fuzzy-match each other.
MAX_KICKOFF_DELTA = timedelta(hours=18)

# Ignore ladder levels thinner than this many shares — dust that cannot be hit.
MIN_LEVEL_SHARES = 1.0
# Never claim a size the caller could not realistically place.
MAX_SIZE_SHARES = float(os.getenv('ARB_MAX_SIZE_SHARES', '5000'))

OUTCOMES = ('home', 'draw', 'away')


# ---------------------------------------------------------------------------
# Name normalisation + fixture join
# ---------------------------------------------------------------------------

_STOPWORDS = (
    r'\b(fc|cf|sc|ac|ss|as|afc|kf|nk|fk|sk|bk|if|cd|ca|cs|sv|tsv|vfb|vfl|rb|bv|'
    r'us|ec|fr|cr|club|de|da|do|dos|del|the|and)\b'
)


def norm_team(s: str) -> str:
    """Accent-folded, stopword-stripped team name for cross-venue matching.

    Accent folding is not cosmetic: Polymarket writes "KF Egnatia Rrogozhinë",
    Kalshi writes "Egnatia Rrogozhine". Without NFKD the 'ë' is stripped as a
    non-ascii char and the two names disagree on the final letter.
    """
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(_STOPWORDS, ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def fixture_key(home: str, away: str) -> str:
    return f'{norm_team(home)}|{norm_team(away)}'


def split_vs(title: str) -> tuple[str, str] | None:
    m = re.match(r'^\s*(.+?)\s+vs\.?\s+(.+?)\s*$', title or '', re.IGNORECASE)
    if not m:
        return None
    h, a = m.group(1).strip(), m.group(2).strip()
    return (h, a) if h and a else None


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ladders
# ---------------------------------------------------------------------------

Ladder = list[tuple[float, float]]   # [(price, shares)] sorted cheapest first


def _clean_ladder(levels: Ladder) -> Ladder:
    out = [(p, s) for p, s in levels if s >= MIN_LEVEL_SHARES and 0.0 < p < 1.0]
    return sorted(out, key=lambda x: x[0])


def walk(ladder: Ladder, shares: float) -> tuple[float, float] | None:
    """Cost in dollars to buy `shares` walking the ladder. None if too thin."""
    need, cost = shares, 0.0
    for price, avail in ladder:
        if need <= 1e-9:
            break
        take = min(need, avail)
        cost += take * price
        need -= take
    if need > 1e-9:
        return None
    return cost, cost / shares


def depth(ladder: Ladder) -> float:
    return sum(s for _, s in ladder)


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def pm_fee(shares: float, vwap: float) -> float:
    """Polymarket taker fee in dollars. Continuous, no rounding."""
    p = min(max(vwap, 0.0), 1.0)
    return shares * PM_FEE_RATE * p * (1.0 - p)


def kalshi_fee(shares: float, vwap: float) -> float:
    """Kalshi taker fee in dollars, rounded UP to the whole cent per order.

    The ceiling is not a rounding detail — on a 10-share order at p=0.50 the
    formula gives $0.0175 and the charge is $0.01... but on a 1-share order it
    gives $0.0004 and the charge is still $0.01, i.e. 100x the nominal rate.
    Small sizes are where this quietly eats an arb.
    """
    p = min(max(vwap, 0.0), 1.0)
    raw = KALSHI_FEE_RATE * shares * p * (1.0 - p)
    # Round before the ceiling: 0.07*100*0.5*0.5 is 1.7500000000000002 in
    # binary, and ceil() on that charges an extra cent that Kalshi does not.
    return math.ceil(round(raw * 100.0, 9)) / 100.0


FEE_FUNCS = {'polymarket': pm_fee, 'kalshi': kalshi_fee}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    venue: str
    outcome: str          # 'home' | 'draw' | 'away'
    side: str             # 'YES' | 'NO'
    ladder: Ladder
    ref: str              # token id / ticker, for the audit trail

    def label(self) -> str:
        return f'{self.venue}:{self.outcome}:{self.side}'


@dataclass
class Fixture:
    home: str
    away: str
    kickoff: datetime | None
    competition: str | None = None
    pm_event: dict | None = None
    kalshi_event: dict | None = None
    # (venue, outcome, side) -> Leg
    legs: dict[tuple[str, str, str], Leg] = field(default_factory=dict)

    def title(self) -> str:
        return f'{self.home} vs {self.away}'


@dataclass
class Arb:
    fixture: Fixture
    kind: str                    # 'DUTCH' | 'PAIR'
    legs: list[Leg]
    shares: float
    cost: float                  # dollars, incl. fees
    fees: float
    payout: float                # dollars (== shares)
    profit: float
    profit_pp: float             # profit per share, in probability points
    vwaps: list[float]

    def describe(self) -> str:
        parts = [f'{l.label()}@{v:.4f}' for l, v in zip(self.legs, self.vwaps)]
        return ' + '.join(parts)


# ---------------------------------------------------------------------------
# Polymarket fetch
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, retries: int = 3, timeout: int = 15):
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={'accept': 'application/json'})
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries:
                raise RuntimeError(f'GET {url} failed: {e}') from e
            time.sleep(delay)
            delay *= 2.0


def _json_array(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            p = json.loads(v)
            return p if isinstance(p, list) else None
        except json.JSONDecodeError:
            return None
    return None


def fetch_pm_fixtures(days: int) -> dict[str, Fixture]:
    """PM football events in the window, keyed by normalised fixture key.

    Gamma only returns the negRisk per-match events when end_date_min/max are
    plain YYYY-MM-DD — ISO timestamps silently drop them (see paper_trader).
    """
    now = datetime.now(timezone.utc)
    date_min = now.strftime('%Y-%m-%d')
    date_max = (now + timedelta(days=max(days, 1))).strftime('%Y-%m-%d')

    events: list[dict] = []
    # Gamma 422s past offset 2000; stop there rather than crash the scan.
    for offset in range(0, 2000, 100):
        try:
            page = _get(f'{GAMMA_API}/events', params={
                'closed': 'false', 'active': 'true', 'limit': 100, 'offset': offset,
                'end_date_min': date_min, 'end_date_max': date_max,
            })
        except RuntimeError as e:
            log.warning(f'[pm] /events stopped at offset {offset}: {e}')
            break
        if not isinstance(page, list) or not page:
            break
        events.extend(page)
    log.info(f'[pm] {len(events)} events in {date_min} → {date_max}')

    out: dict[str, Fixture] = {}
    for e in events:
        teams = split_vs(e.get('title', ''))
        if not teams:
            continue
        markets = e.get('markets') or []
        if len(markets) < 2:
            continue
        home, away = teams
        # endDate, not startDate: PM's startDate is when the market opened,
        # often two weeks before the fixture. endDate tracks resolution, which
        # sits just after the final whistle.
        fx = Fixture(home=home, away=away,
                     kickoff=_parse_dt(e.get('endDate') or e.get('startDate')),
                     pm_event=e)

        for m in markets:
            outcome = _pm_outcome(m.get('question', ''), home, away)
            if outcome is None:
                continue
            toks = _json_array(m.get('clobTokenIds'))
            labels = _json_array(m.get('outcomes'))
            if not toks or not labels or len(toks) != len(labels):
                continue
            for tok, lbl in zip(toks, labels):
                side = 'YES' if str(lbl).strip().lower() == 'yes' else 'NO'
                fx.legs[('polymarket', outcome, side)] = Leg(
                    venue='polymarket', outcome=outcome, side=side,
                    ladder=[], ref=str(tok))
        if len(fx.legs) >= 4:
            out[fixture_key(home, away)] = fx
    log.info(f'[pm] {len(out)} fixture-shaped events with usable markets')
    return out


def _pm_outcome(question: str, home: str, away: str) -> str | None:
    """PM asks one binary question per outcome: 'Will X win on DATE?' /
    'Will X vs. Y end in a draw?'. Map that back to home/draw/away."""
    q = (question or '').lower()
    if 'draw' in q:
        return 'draw'
    m = re.search(r'will\s+(.+?)\s+win\b', q)
    if not m:
        return None
    who = norm_team(m.group(1))
    if not who:
        return None
    hn, an = norm_team(home), norm_team(away)
    if who == hn:
        return 'home'
    if who == an:
        return 'away'
    # PM sometimes shortens relative to the event title.
    if hn.startswith(who) or who.startswith(hn):
        return 'home'
    if an.startswith(who) or who.startswith(an):
        return 'away'
    return None


# ---------------------------------------------------------------------------
# Kalshi fetch
# ---------------------------------------------------------------------------

def fetch_kalshi_fixtures(cache_dir: Path) -> dict[str, Fixture]:
    from stage_i_kalshi import discover_soccer_series, fetch_events, split_teams

    series = discover_soccer_series(cache_dir, ['GAME'])
    log.info(f'[kalshi] walking {len(series)} game series')

    out: dict[str, Fixture] = {}
    for s in series:
        try:
            events = fetch_events(s['ticker'])
        except RuntimeError as e:
            log.warning(f'[kalshi] {s["ticker"]}: {e}')
            continue
        for ev in events:
            teams = split_teams(ev.get('title', ''))
            if not teams:
                continue
            home, away = teams
            meta = ev.get('product_metadata') or {}
            fx = Fixture(home=home, away=away, kickoff=None,
                         competition=meta.get('competition'), kalshi_event=ev)
            for m in ev.get('markets') or []:
                oc = _kalshi_outcome(m, home, away)
                if oc is None:
                    continue
                if fx.kickoff is None:
                    fx.kickoff = _kalshi_kickoff(m, ev)
                # Both sides share one ticker; the ladders are derived in
                # load_books from the single orderbook call.
                for side in ('YES', 'NO'):
                    fx.legs[('kalshi', oc, side)] = Leg(
                        venue='kalshi', outcome=oc, side=side,
                        ladder=[], ref=m['ticker'])
            if len(fx.legs) >= 4:
                out[fixture_key(home, away)] = fx
    log.info(f'[kalshi] {len(out)} fixtures')
    return out


_TICKER_DATE = re.compile(r'-(\d{2})([A-Z]{3})(\d{2})')
_MONTHS = {m: i for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
     'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], start=1)}


def _kalshi_kickoff(market: dict, event: dict) -> datetime | None:
    """Match time for a Kalshi game market.

    NOT close_time — that is the settlement window and sits about two weeks
    after the fixture (a Jul 22 match closes Aug 5). Using it silently pushes
    every fixture outside the join's time gate and the join returns zero.
    `occurrence_datetime` is the expected end of the match, which is close
    enough for a same-fixture gate.
    """
    for key in ('occurrence_datetime', 'expected_expiration_time'):
        dt = _parse_dt(market.get(key))
        if dt is not None:
            return dt
    m = _TICKER_DATE.search(event.get('event_ticker') or '')
    if m and m.group(2) in _MONTHS:
        yy, mon, dd = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
        return datetime(2000 + yy, mon, dd, 12, 0, tzinfo=timezone.utc)
    return None


def _kalshi_outcome(market: dict, home: str, away: str) -> str | None:
    label = re.sub(r'^\s*reg\s*time\s*:\s*', '',
                   market.get('yes_sub_title') or '', flags=re.IGNORECASE).strip()
    if not label:
        return None
    if label.lower() in ('tie', 'draw'):
        return 'draw'
    ln, hn, an = norm_team(label), norm_team(home), norm_team(away)
    if ln == hn or hn.startswith(ln) or ln.startswith(hn):
        return 'home'
    if ln == an or an.startswith(ln) or ln.startswith(an):
        return 'away'
    return None


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def join(pm: dict[str, Fixture], kal: dict[str, Fixture]) -> list[Fixture]:
    """Merge the two venues' fixtures. Name match (exact or fuzzy) gated on a
    kickoff time that agrees — the gate is what stops 'Botafogo' matching the
    wrong Botafogo."""
    merged: list[Fixture] = []
    pm_keys = list(pm)
    used: set[str] = set()

    for k, kfx in kal.items():
        cand = k if k in pm else None
        if cand is None:
            close = difflib.get_close_matches(k, pm_keys, n=1, cutoff=FUZZY_CUTOFF)
            cand = close[0] if close else None
        if cand is None or cand in used:
            continue
        pfx = pm[cand]

        if pfx.kickoff and kfx.kickoff:
            if abs(pfx.kickoff - kfx.kickoff) > MAX_KICKOFF_DELTA:
                log.debug(f'join rejected on time: {kfx.title()} / {pfx.title()}')
                continue

        used.add(cand)
        fx = Fixture(home=pfx.home, away=pfx.away,
                     kickoff=pfx.kickoff or kfx.kickoff,
                     competition=kfx.competition,
                     pm_event=pfx.pm_event, kalshi_event=kfx.kalshi_event)
        fx.legs = {**pfx.legs, **kfx.legs}
        merged.append(fx)

    log.info(f'[join] {len(merged)} fixtures on both venues '
             f'(pm {len(pm)}, kalshi {len(kal)})')
    return merged


# ---------------------------------------------------------------------------
# Book loading
# ---------------------------------------------------------------------------

def load_pm_book(leg: Leg) -> None:
    try:
        book = _get(f'{CLOB_API}/book', params={'token_id': leg.ref}, retries=1, timeout=10)
    except RuntimeError:
        return
    asks = [(float(a['price']), float(a['size'])) for a in (book.get('asks') or [])]
    leg.ladder = _clean_ladder(asks)


def load_kalshi_book(ticker: str) -> tuple[Ladder, Ladder]:
    """Returns (buy_yes_ladder, buy_no_ladder) for one Kalshi market.

    Kalshi publishes resting BIDS on both sides. To buy YES you lift the NO
    bids at 1-p; to buy NO you lift the YES bids at 1-p.
    """
    try:
        r = _get(f'{KALSHI_API}/markets/{ticker}/orderbook',
                 params={'depth': 20}, retries=1, timeout=10)
    except RuntimeError:
        return [], []
    ob = r.get('orderbook_fp') or {}
    yes_bids = [(float(p), float(s)) for p, s in (ob.get('yes_dollars') or [])]
    no_bids = [(float(p), float(s)) for p, s in (ob.get('no_dollars') or [])]
    buy_yes = _clean_ladder([(1.0 - p, s) for p, s in no_bids])
    buy_no = _clean_ladder([(1.0 - p, s) for p, s in yes_bids])
    return buy_yes, buy_no


def load_books(fixtures: list[Fixture], workers: int = 8) -> None:
    pm_legs = [l for f in fixtures for l in f.legs.values() if l.venue == 'polymarket']
    kal_tickers = sorted({l.ref for f in fixtures for l in f.legs.values()
                          if l.venue == 'kalshi'})
    log.info(f'[books] {len(pm_legs)} PM tokens, {len(kal_tickers)} Kalshi markets')

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(load_pm_book, pm_legs))
        kal_books = dict(zip(kal_tickers, ex.map(load_kalshi_book, kal_tickers)))

    for f in fixtures:
        for leg in f.legs.values():
            if leg.venue != 'kalshi':
                continue
            buy_yes, buy_no = kal_books.get(leg.ref, ([], []))
            leg.ladder = buy_yes if leg.side == 'YES' else buy_no


# ---------------------------------------------------------------------------
# Arb search
# ---------------------------------------------------------------------------

def price_basket(legs: list[Leg], shares: float, use_fees: bool) -> Arb | None:
    """Cost of buying `shares` of every leg. Payout is exactly `shares`."""
    cost, fees, vwaps = 0.0, 0.0, []
    for leg in legs:
        w = walk(leg.ladder, shares)
        if w is None:
            return None
        leg_cost, vwap = w
        cost += leg_cost
        vwaps.append(vwap)
        if use_fees:
            fees += FEE_FUNCS[leg.venue](shares, vwap)
    total = cost + fees
    profit = shares - total
    return Arb(fixture=None, kind='', legs=legs, shares=shares,
               cost=total, fees=fees, payout=shares, profit=profit,
               profit_pp=100.0 * profit / shares, vwaps=vwaps)


def best_size(legs: list[Leg], use_fees: bool) -> Arb | None:
    """Maximise total dollar profit over the ladder breakpoints.

    Profit per share falls as size grows (VWAP worsens) while total profit can
    still rise, so neither the smallest nor the largest size is automatically
    right — evaluate at every level boundary and take the best.
    """
    cap = min([depth(l.ladder) for l in legs] + [MAX_SIZE_SHARES])
    if cap < MIN_LEVEL_SHARES:
        return None

    breakpoints: set[float] = {cap}
    for leg in legs:
        run = 0.0
        for _, size in leg.ladder:
            run += size
            if run <= cap:
                breakpoints.add(run)

    best: Arb | None = None
    for n in sorted(breakpoints):
        if n < MIN_LEVEL_SHARES:
            continue
        a = price_basket(legs, n, use_fees)
        # Half a cent of total profit is float noise, not an opportunity.
        if a and a.profit > 0.005 and (best is None or a.profit > best.profit):
            best = a
    return best


def find_arbs(fx: Fixture, use_fees: bool, min_profit_pp: float) -> list[Arb]:
    found: list[Arb] = []

    # DUTCH — one YES leg per outcome, cheapest venue per outcome.
    dutch: list[Leg] = []
    for oc in OUTCOMES:
        opts = [fx.legs.get((v, oc, 'YES')) for v in ('polymarket', 'kalshi')]
        opts = [l for l in opts if l and l.ladder]
        if not opts:
            dutch = []
            break
        dutch.append(min(opts, key=lambda l: l.ladder[0][0]))
    if len(dutch) == 3:
        a = best_size(dutch, use_fees)
        if a and a.profit_pp >= min_profit_pp:
            a.fixture, a.kind = fx, 'DUTCH'
            found.append(a)

    # PAIR — YES at one venue, NO at the other, same outcome.
    for oc in OUTCOMES:
        for va, vb in (('polymarket', 'kalshi'), ('kalshi', 'polymarket')):
            ly = fx.legs.get((va, oc, 'YES'))
            ln = fx.legs.get((vb, oc, 'NO'))
            if not (ly and ln and ly.ladder and ln.ladder):
                continue
            a = best_size([ly, ln], use_fees)
            if a and a.profit_pp >= min_profit_pp:
                a.fixture, a.kind = fx, 'PAIR'
                found.append(a)

    return found


# ---------------------------------------------------------------------------
# Settlement-rule audit
# ---------------------------------------------------------------------------

REG_TIME_PM = re.compile(r'first 90 minutes of regular play', re.I)
REG_TIME_KALSHI = re.compile(r'does not include extra time', re.I)


def audit_rules(fixtures: list[Fixture]) -> None:
    """Re-verify that both venues still settle 1X2 on regulation time.

    This is the assumption the entire module rests on. It is cheap to check
    and catastrophic to get wrong, so it is checked rather than believed.
    """
    bad = 0
    for fx in fixtures:
        pm_desc = ''
        for m in (fx.pm_event or {}).get('markets', []):
            pm_desc = m.get('description') or ''
            if pm_desc:
                break
        k_rules = ''
        for m in (fx.kalshi_event or {}).get('markets', []):
            k_rules = m.get('rules_primary') or ''
            if k_rules:
                break
        pm_ok = bool(REG_TIME_PM.search(pm_desc))
        k_ok = bool(REG_TIME_KALSHI.search(k_rules))
        if not (pm_ok and k_ok):
            bad += 1
            log.warning(f'RULE MISMATCH {fx.title()}: pm_regtime={pm_ok} kalshi_regtime={k_ok}')
    if bad == 0:
        log.info(f'[audit] settlement rules agree on all {len(fixtures)} fixtures '
                 f'(both regulation time)')
    else:
        log.error(f'[audit] {bad}/{len(fixtures)} fixtures FAILED the regulation-time '
                  f'check — do not trade these baskets')


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(arbs: list[Arb], fixtures: list[Fixture], use_fees: bool) -> None:
    print()
    print('=' * 100)
    mode = 'GROSS (fees ignored)' if not use_fees else 'NET of taker fees'
    print(f'CROSS-VENUE ARB — Polymarket vs Kalshi — {mode}')
    print(f'{len(fixtures)} fixtures on both venues')
    print('=' * 100)

    if not arbs:
        print('\nNo arbitrage found.\n')
        _print_near_misses(fixtures, use_fees)
        return

    for a in sorted(arbs, key=lambda x: -x.profit_pp):
        ko = a.fixture.kickoff.strftime('%Y-%m-%d %H:%M') if a.fixture.kickoff else '?'
        print(f'\n{a.kind:5s}  {a.fixture.title()}   [{a.fixture.competition or "?"}]  {ko}Z')
        print(f'       {a.describe()}')
        print(f'       size {a.shares:.0f} shares · cost ${a.cost:.2f} '
              f'(fees ${a.fees:.2f}) · payout ${a.payout:.2f}')
        print(f'       PROFIT ${a.profit:.2f}  ({a.profit_pp:+.2f}pp per share)')
    print()


def _print_near_misses(fixtures: list[Fixture], use_fees: bool, n: int = 8) -> None:
    """The distribution matters more than the (usually empty) hit list: it says
    how far from arb the two venues actually are."""
    rows = []
    for fx in fixtures:
        legs = []
        for oc in OUTCOMES:
            opts = [fx.legs.get((v, oc, 'YES')) for v in ('polymarket', 'kalshi')]
            opts = [l for l in opts if l and l.ladder]
            if not opts:
                legs = []
                break
            legs.append(min(opts, key=lambda l: l.ladder[0][0]))
        if len(legs) != 3:
            continue
        total = sum(l.ladder[0][0] for l in legs)
        venues = '/'.join(l.venue[0].upper() for l in legs)
        rows.append((total, fx, venues))
    if not rows:
        print('No fixture had a complete 1X2 on both venues.\n')
        return
    rows.sort(key=lambda r: r[0])
    print(f'Closest {min(n, len(rows))} of {len(rows)} complete 1X2 baskets '
          f'(sum of best asks, cheapest venue per outcome; 1.0000 = breakeven '
          f'before fees):\n')
    for total, fx, venues in rows[:n]:
        print(f'  {total:.4f}  [{venues}]  {fx.title()}')
    med = rows[len(rows) // 2][0]
    print(f'\n  median basket {med:.4f}  ({(med - 1) * 100:+.2f}pp vs breakeven)\n')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=3, help='fixture window (default 3)')
    ap.add_argument('--min-profit-pp', type=float, default=0.0,
                    help='only report baskets above this net profit, in pp')
    ap.add_argument('--gross', action='store_true',
                    help='ignore fees — upper bound on what is theoretically there')
    ap.add_argument('--audit-rules', action='store_true',
                    help='re-verify both venues settle on regulation time')
    ap.add_argument('--json', type=Path, default=None, help='dump results as JSON')
    ap.add_argument('--cache-dir', type=Path,
                    default=Path(__file__).parent.parent / 'ingest' / '.cache' / 'kalshi')
    args = ap.parse_args()

    use_fees = not args.gross

    pm = fetch_pm_fixtures(args.days)
    kal = fetch_kalshi_fixtures(args.cache_dir)
    fixtures = join(pm, kal)
    if not fixtures:
        log.warning('no overlapping fixtures — nothing to compare')
        return

    if args.audit_rules:
        audit_rules(fixtures)

    load_books(fixtures)
    fixtures = [f for f in fixtures if any(l.ladder for l in f.legs.values())]
    log.info(f'[books] {len(fixtures)} fixtures have at least one live ladder')

    arbs: list[Arb] = []
    for fx in fixtures:
        arbs.extend(find_arbs(fx, use_fees, args.min_profit_pp))

    report(arbs, fixtures, use_fees)

    if args.json:
        args.json.write_text(json.dumps([{
            'kind': a.kind, 'fixture': a.fixture.title(),
            'competition': a.fixture.competition,
            'kickoff': a.fixture.kickoff.isoformat() if a.fixture.kickoff else None,
            'legs': [{'venue': l.venue, 'outcome': l.outcome, 'side': l.side,
                      'ref': l.ref, 'vwap': v}
                     for l, v in zip(a.legs, a.vwaps)],
            'shares': a.shares, 'cost': a.cost, 'fees': a.fees,
            'profit': a.profit, 'profit_pp': a.profit_pp,
        } for a in arbs], indent=2))
        log.info(f'wrote {args.json}')


if __name__ == '__main__':
    main()
