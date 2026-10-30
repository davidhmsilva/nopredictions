#!/usr/bin/env python3
"""
Cross-venue arbitrage on every sport both exchanges list: Polymarket vs Kalshi.

The football-only detector (cross_venue_arb.py, 2026-07-22) found nothing on
57 fixtures: a gross ceiling of one tick against a 3pp fee bar. This is the
re-run, widened to the US sports and re-priced, because two things changed:

THE FEE IS NOW PER MARKET, AND LOWER
    Polymarket publishes `feeSchedule` on every market and charges exactly
    that. Read live on 2026-10-05 and checked against real fills
    (usdcSize - size x price, divided by size x p(1-p)):

        MLB, NHL, soccer moneylines   sports_fees_v3              0.05
        NFL, college football         sports_fees_nfl_cfb_oct26   0.03
        some NFL games                zero_fees                   0

    The docs page still says 0.05 for sports. The market field is what the
    exchange charges, so that is what is used. Makers pay nothing.

    Kalshi scales its 0.07 taker / 0.0175 maker by the series' `fee_multiplier`:
    KXMLBGAME is 0.5, the others 1. A Kalshi fee rounds UP to the cent per order.

    So the bar is read off every leg, never a constant. At p=0.50 an NFL pair
    now needs 0.75 + 1.75 = 2.50pp, an MLB pair 1.25 + 0.875 = 2.125pp, against
    the 3.00pp that killed football in July.

A BASKET IS AN ARB ONLY IF IT PAYS IN EVERY OUTCOME
    Every leg carries its payout in every scenario, and a basket is any two or
    three legs whose WORST-case payout is at least $1 with no redundant leg.
    That one rule covers the YES-here/NO-there pair, the US two-team
    complement (home on one exchange, away on the other), the three-way dutch,
    and the intra-venue baskets (Kalshi's two team markets, Polymarket's three
    1X2 questions), which are labelled apart.

    The rules were read on 2026-10-05:
      * NFL tie: Polymarket 50-50, Kalshi $0.50 to each team. Consistent, so a
        tie scenario is carried for the NFL and every basket still pays $1.
      * Soccer: both venues settle the 1X2 on regulation time
        (cross_venue_arb.audit_rules).
      * NHL: Polymarket counts overtime and the shootout; Kalshi pays "the
        team who wins the game". MLB, CFB, NBA: no tie path.
    ⚠️ NOT covered, and not riskless: a game postponed beyond 48 hours or
       cancelled. Polymarket waits for the make-up game (or pays 50-50),
       Kalshi "resolves to a fair price". A basket held through that can lose.

A QUOTE CAN BE OLD BY THE TIME THE OTHER ONE ARRIVES
    The two venues' books are read seconds apart, and in play a price moves
    in seconds. A crossing seen once is re-read on both venues at once and
    counted as `confirmed` only if it survives. The unconfirmed count is kept,
    because it measures how often a naive scanner would have reported an arb
    that was not there.

THE MAKER WINDOW IS NOT AN ARB
    Also recorded, for each two-leg cross-venue basket: the cost of resting a
    bid on one leg (maker: free on Polymarket, 0.0175 x multiplier on Kalshi)
    and taking the other. A positive window means a resting order WOULD lock a
    profit IF it were filled with the other venue unchanged. Fills come
    exactly when the price moves through you, so this is market making with a
    hedge, adverse selection included -- what arb bots actually do, measured
    so it is not mistaken for the risk-free thing.

Read-only. Places nothing.

    python cross_venue_arb_sports.py --once                    # one snapshot
    python cross_venue_arb_sports.py --minutes 300 --interval 60
    python cross_venue_arb_sports.py --sports nfl,mlb --once
    python cross_venue_arb_sports.py --summary <file.jsonl>
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import re
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).parent))

import venues  # noqa: E402
from cross_venue_arb import _clean_ladder, _json_array, _pm_outcome, walk  # noqa: E402

log = logging.getLogger('arb_sports')

GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports'
ET = ZoneInfo('America/New_York')

KALSHI_TAKER = 0.07
KALSHI_MAKER = 0.0175

OUT_DIR = Path(__file__).parent / 'data' / 'arb_scan'

#: The US sports, as the site's boards have them (site/app/lib/sports.ts).
#: `tie` = a scenario where both team markets pay 0.5 on both venues.
#: `hours` = how long after the start a game is still treated as live.
US = {
    'nfl': {'espn': 'football/nfl', 'kalshi': 'KXNFLGAME', 'pm_tag': 450, 'tie': True, 'hours': 3.6},
    'cfb': {'espn': 'football/college-football', 'groups': ['80', '81'], 'kalshi': 'KXNCAAFGAME',
            'pm_tag': 100351, 'tie': False, 'hours': 3.8},
    'mlb': {'espn': 'baseball/mlb', 'kalshi': 'KXMLBGAME', 'pm_tag': 100381, 'tie': False, 'hours': 3.5},
    'nba': {'espn': 'basketball/nba', 'kalshi': 'KXNBAGAME', 'pm_tag': 745, 'tie': False, 'hours': 2.7},
    'nhl': {'espn': 'hockey/nhl', 'kalshi': 'KXNHLGAME', 'pm_tag': 899, 'tie': False, 'hours': 3.0},
    'wnba': {'espn': 'basketball/wnba', 'kalshi': 'KXWNBAGAME', 'pm_tag': 100254, 'tie': False, 'hours': 2.5},
}
SOCCER_TAG = 100350
ALL_SPORTS = ('soccer',) + tuple(US)

#: Earliest and latest start a game may have to be scanned.
LOOKBACK_H = 5
HORIZON_H = 48

MAX_SIZE_SHARES = 5000.0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(url: str, params=None, retries: int = 3, timeout: int = 20):
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429 and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2


def _post(url: str, body, retries: int = 2, timeout: int = 30):
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=body, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _dt(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def norm(s) -> str:
    """The site's US-sports key: lower case, '&' -> 'and', alphanumerics only."""
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', s.lower().replace('&', 'and'))


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def pm_rate(market: dict) -> float:
    """Polymarket's taker rate for THIS market. Never the docs' constant."""
    if market.get('feesEnabled') is False:
        return 0.0
    fs = market.get('feeSchedule') or {}
    rate = _num(fs.get('rate'))
    if rate is None:
        # No schedule published: assume the documented sports rate, which is
        # the expensive side of every rate seen live.
        return 0.05
    if _num(fs.get('exponent')) not in (None, 1.0):
        log.warning('fee exponent %s on %s: formula assumed linear', fs.get('exponent'),
                    market.get('conditionId'))
    return rate


def fee_dollars(venue: str, rate: float, shares: float, price: float) -> float:
    p = min(max(price, 0.0), 1.0)
    raw = rate * shares * p * (1.0 - p)
    if venue == venues.KALSHI:
        # Up to the cent, per order. Round first: 0.07*100*0.25 is
        # 1.7500000000000002 in binary.
        return math.ceil(round(raw * 100.0, 9)) / 100.0
    return raw


# ---------------------------------------------------------------------------
# Legs, games, baskets
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    venue: str
    ref: str               # PM token id / Kalshi ticker
    market: str            # PM condition id / Kalshi ticker: one book per market
    side: str              # 'YES' / 'NO' (a PM team token is a YES on that team)
    label: str
    pay: dict              # scenario -> payout per share
    taker: float           # fee rate
    maker: float
    ladder: list = field(default_factory=list)   # asks, cheapest first
    bid: float | None = None
    bid_size: float | None = None

    @property
    def ask(self) -> float | None:
        return self.ladder[0][0] if self.ladder else None


@dataclass
class Game:
    sport: str
    title: str
    start: datetime | None
    scenarios: tuple
    legs: list
    baskets: list = field(default_factory=list)   # [(leg indices, min payout)]

    def state(self, now: datetime) -> str:
        if self.start is None:
            return '?'
        if now < self.start:
            return 'pre'
        hours = US[self.sport]['hours'] if self.sport in US else 2.2
        return 'live' if now < self.start + timedelta(hours=hours) else 'post'


def enumerate_baskets(legs: list, scenarios: tuple) -> list:
    """Every 2- or 3-leg set paying >= $1 in every scenario, with no redundant
    leg and never both sides of one book (that is the book's own spread)."""
    out = []
    for k in (2, 3):
        for combo in itertools.combinations(range(len(legs)), k):
            ls = [legs[i] for i in combo]
            books = [(l.venue, l.market) for l in ls]
            if len(set(books)) < len(books):
                continue
            m = min(sum(l.pay[s] for l in ls) for s in scenarios)
            if m < 1 - 1e-9:
                continue
            redundant = any(
                min(sum(l.pay[s] for j, l in enumerate(ls) if j != i) for s in scenarios) >= 1 - 1e-9
                for i in range(k))
            if not redundant:
                out.append((combo, m))
    return out


def cross(legs: list) -> bool:
    return len({l.venue for l in legs}) > 1


def price_at(legs: list, shares: float, m: float):
    cost = fees = 0.0
    vwaps = []
    for l in legs:
        w = walk(l.ladder, shares)
        if w is None:
            return None
        c, v = w
        cost += c
        fees += fee_dollars(l.venue, l.taker, shares, v)
        vwaps.append(v)
    profit = shares * m - cost - fees
    return {'shares': shares, 'cost': cost, 'fees': fees, 'profit': profit,
            'profit_pp': 100.0 * profit / shares, 'vwaps': vwaps}


def best_size(legs: list, m: float):
    """Most dollars over every ladder breakpoint, fees at the sized quantity."""
    cap = min([sum(s for _, s in l.ladder) for l in legs] + [MAX_SIZE_SHARES])
    if cap < 1:
        return None
    points = {cap}
    for l in legs:
        run = 0.0
        for _, s in l.ladder:
            run += s
            if run <= cap:
                points.add(run)
    # Small sizes too: Kalshi's per-order cent rounding is worst there, and a
    # one-share "arb" that a cent of fee wipes out should be seen to vanish.
    points.update(x for x in (1, 10, 100) if x <= cap)
    best = None
    for n in sorted(points):
        r = price_at(legs, n, m)
        if r and r['profit'] > 0.005 and (best is None or r['profit'] > best['profit']):
            best = r
    return best


def evaluate(g: Game) -> dict:
    """One game, one moment: the closest basket to an arb, and any arb."""
    best_cross = best_intra = None      # (gross_pp, net_pp, basket)
    arbs = []
    maker = None
    for combo, m in g.baskets:
        ls = [g.legs[i] for i in combo]
        if any(l.ask is None for l in ls):
            continue
        asks = [l.ask for l in ls]
        gross = 100.0 * (m - sum(asks))
        net = gross - 100.0 * sum(l.taker * a * (1 - a) for l, a in zip(ls, asks))
        row = (gross, net, combo)
        if cross(ls):
            if best_cross is None or net > best_cross[1]:
                best_cross = row
        elif best_intra is None or net > best_intra[1]:
            best_intra = row
        if gross > 0:
            sized = best_size(ls, m)
            if sized:
                arbs.append({'legs': [_leg_json(l) for l in ls], 'cross': cross(ls),
                             'gross_pp_tob': round(gross, 3), **_round(sized)})
        # The maker window: rest a bid on one leg, take the other.
        if len(ls) == 2 and cross(ls):
            for i in (0, 1):
                mk, tk = ls[i], ls[1 - i]
                if mk.bid is None or not (0 < mk.bid < 1):
                    continue
                c = (mk.bid + mk.maker * mk.bid * (1 - mk.bid)
                     + tk.ask + tk.taker * tk.ask * (1 - tk.ask))
                w = 100.0 * (m - c)
                if maker is None or w > maker[0]:
                    maker = (w, f'rest {mk.label} @{mk.bid:.3f} + take {tk.label} @{tk.ask:.3f}')

    def basket(row):
        if row is None:
            return None
        gross, net, combo = row
        return {'gross_pp': round(gross, 3), 'net_pp': round(net, 3),
                'legs': ' + '.join(f'{g.legs[i].label}@{g.legs[i].ask:.3f}' for i in combo)}

    return {'best_cross': basket(best_cross), 'best_intra': basket(best_intra), 'arbs': arbs,
            'maker_pp': None if maker is None else round(maker[0], 3),
            'maker_how': None if maker is None else maker[1]}


def _leg_json(l: Leg) -> dict:
    return {'venue': l.venue, 'label': l.label, 'ref': l.ref, 'side': l.side,
            'ask': l.ask, 'depth': round(sum(s for _, s in l.ladder), 1), 'fee_rate': l.taker}


def _tob(l: Leg, scenarios) -> list:
    ask_size = l.ladder[0][1] if l.ladder else None
    return [l.label, l.venue, l.ref, l.market, l.side, [l.pay[s] for s in scenarios],
            l.taker, l.maker, l.bid, l.bid_size, l.ask, ask_size]


def _round(d: dict) -> dict:
    return {k: ([round(x, 4) for x in v] if isinstance(v, list) else round(v, 4))
            for k, v in d.items()}


# ---------------------------------------------------------------------------
# Kalshi: fee multipliers, events
# ---------------------------------------------------------------------------

def kalshi_multipliers() -> dict:
    d = venues._paced_get('/series', {'category': 'Sports'})
    out = {}
    for s in d.get('series') or []:
        mult = _num(s.get('fee_multiplier'))
        out[s.get('ticker')] = 1.0 if mult is None else mult
    return out


def kalshi_events(series: str) -> list:
    out, cursor = [], ''
    for _ in range(5):
        params = {'series_ticker': series, 'status': 'open', 'limit': 200,
                  'with_nested_markets': 'true'}
        if cursor:
            params['cursor'] = cursor
        d = venues._paced_get('/events', params)
        evs = d.get('events') or []
        out.extend(evs)
        cursor = d.get('cursor') or ''
        if not cursor or not evs:
            break
    return out


def kalshi_legs(series: str, mult: float, team_markets: dict, scenarios: tuple,
                tie_pays_half: bool) -> list:
    """{scenario: market} -> YES and NO legs on each Kalshi market."""
    legs = []
    for sc, m in team_markets.items():
        t = m['ticker']
        name = m.get('yes_sub_title') or t.rsplit('-', 1)[-1]
        yes = {s: (1.0 if s == sc else 0.0) for s in scenarios}
        if tie_pays_half and 'tie' in scenarios:
            yes['tie'] = 0.5
        no = {s: 1.0 - v for s, v in yes.items()}
        for side, pay in (('YES', yes), ('NO', no)):
            legs.append(Leg(venue=venues.KALSHI, ref=t, market=t, side=side,
                            label=f'K:{side} {name}', pay=pay,
                            taker=KALSHI_TAKER * mult, maker=KALSHI_MAKER * mult))
    return legs


# ---------------------------------------------------------------------------
# US sports: ESPN is the spine, both venues are placed on it
# ---------------------------------------------------------------------------

def espn_keys(team: dict) -> set:
    k = {norm(team.get(x)) for x in ('abbreviation', 'location', 'name', 'displayName',
                                     'shortDisplayName')}
    words = (team.get('name') or '').split()
    loc = team.get('location') or ''
    if loc and words:
        k.add(norm(loc + ''.join(w[0] for w in words)))
        k.add(norm(loc + words[0][0]))
    k.discard('')
    return k


def espn_games(sport: str, now: datetime) -> list:
    src = US[sport]
    days = sorted({(now + timedelta(hours=h)).astimezone(ET).strftime('%Y%m%d')
                   for h in range(-LOOKBACK_H - 12, HORIZON_H + 1, 12)})
    out, seen = [], set()
    for day in days:
        for groups in src.get('groups') or [None]:
            params = {'dates': day, 'limit': 500}
            if groups:
                params['groups'] = groups
            try:
                d = _get(f'{ESPN}/{src["espn"]}/scoreboard', params)
            except requests.RequestException as e:
                log.warning('[espn] %s %s: %s', sport, day, e)
                continue
            for e in d.get('events') or []:
                if e.get('id') in seen:
                    continue
                seen.add(e.get('id'))
                comp = (e.get('competitions') or [{}])[0]
                cs = comp.get('competitors') or []
                h = next((c for c in cs if c.get('homeAway') == 'home'), None)
                a = next((c for c in cs if c.get('homeAway') == 'away'), None)
                start = _dt(e.get('date'))
                if not h or not a or start is None:
                    continue
                out.append({'id': e['id'], 'start': start,
                            'home': h['team'], 'away': a['team'],
                            'hk': espn_keys(h['team']), 'ak': espn_keys(a['team'])})
    return out


_KT = re.compile(r'-(\d{2})([A-Z]{3})(\d{2})(\d{4})?')
_MON = {m: i for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], 1)}


def place_kalshi(ev: dict, games: list):
    """Kalshi's ticker carries the ET date (and MLB's ET first pitch, which
    splits a doubleheader). Both markets must name opposite sides of one game."""
    m = _KT.search(ev.get('event_ticker') or '')
    if not m or m.group(2) not in _MON:
        return None
    ymd = f'20{m.group(1)}{_MON[m.group(2)]:02d}{m.group(3)}'
    hhmm = m.group(4)
    sides = [x for x in ev.get('markets') or []
             if not re.match(r'^(TIE|DRAW)$', x['ticker'].rsplit('-', 1)[-1], re.I)]
    if len(sides) != 2:
        return None

    def side(mk, g):
        sub = mk.get('yes_sub_title') or ''
        forms = {norm(mk['ticker'].rsplit('-', 1)[-1]), norm(sub),
                 norm(re.sub(r'\bSt\.?$', 'State', sub))}
        forms.discard('')
        h, a = bool(forms & g['hk']), bool(forms & g['ak'])
        return None if h == a else ('home' if h else 'away')

    hits = []
    for g in games:
        et = g['start'].astimezone(ET)
        if et.strftime('%Y%m%d') != ymd:
            continue
        if hhmm and abs(et.hour * 60 + et.minute - (int(hhmm[:2]) * 60 + int(hhmm[2:]))) > 90:
            continue
        s0, s1 = side(sides[0], g), side(sides[1], g)
        if not s0 or not s1 or s0 == s1:
            continue
        hits.append((g, {s0: sides[0], s1: sides[1]}))
    return hits[0] if len(hits) == 1 else None


def pm_us_events(tag: int, now: datetime) -> list:
    d0 = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    d1 = (now + timedelta(days=10)).strftime('%Y-%m-%d')
    out = []
    for offset in range(0, 1000, 100):
        page = _get(f'{GAMMA}/events', {'tag_id': tag, 'closed': 'false', 'limit': 100,
                                        'offset': offset, 'end_date_min': d0, 'end_date_max': d1})
        out.extend(page or [])
        if not page or len(page) < 100:
            break
    return out


def place_pm(ev: dict, games: list):
    """Sides from each outcome's label against Polymarket's own `teams`
    ordering -- never from a position in a list."""
    ml = next((m for m in ev.get('markets') or []
               if (m.get('sportsMarketType') or '').lower() == 'moneyline'), None)
    teams = ev.get('teams') or []
    start = _dt(ev.get('startTime'))
    if not ml or len(teams) != 2 or start is None or ev.get('ended'):
        return None
    labels, toks = _json_array(ml.get('outcomes')), _json_array(ml.get('clobTokenIds'))
    if not labels or not toks or len(labels) != 2 or len(toks) != 2:
        return None

    def side_of(label):
        t = next((x for x in teams if any(norm(n) == norm(label)
                                          for n in (x.get('alias'), x.get('name'), x.get('abbreviation'))
                                          if n)), None)
        return t.get('ordering') if t and t.get('ordering') in ('home', 'away') else None

    s = [side_of(x) for x in labels]
    if None in s or s[0] == s[1]:
        return None
    by_side = {t.get('ordering'): t for t in teams}

    def matches(t, keys):
        return any(norm(t.get(x)) in keys for x in ('name', 'alias', 'abbreviation') if t.get(x))

    hits = [g for g in games
            if abs((g['start'] - start).total_seconds()) <= 3 * 3600
            and matches(by_side['home'], g['hk']) and matches(by_side['away'], g['ak'])
            and not matches(by_side['home'], g['ak']) and not matches(by_side['away'], g['hk'])]
    if len(hits) != 1:
        return None
    return hits[0], ml, {s[0]: (toks[0], labels[0]), s[1]: (toks[1], labels[1])}


def build_us(sport: str, now: datetime, mults: dict) -> list:
    src = US[sport]
    games = espn_games(sport, now)
    scenarios = ('home', 'away', 'tie') if src['tie'] else ('home', 'away')
    kal = {}
    try:
        for ev in kalshi_events(src['kalshi']):
            hit = place_kalshi(ev, games)
            if hit:
                g, by_side = hit
                kal.setdefault(g['id'], []).append(by_side)
    except Exception as e:      # noqa: BLE001 - one venue failing costs its column
        log.warning('[kalshi] %s: %s', sport, e)
    pm = {}
    try:
        for ev in pm_us_events(src['pm_tag'], now):
            hit = place_pm(ev, games)
            if hit:
                pm.setdefault(hit[0]['id'], []).append(hit[1:])
    except Exception as e:      # noqa: BLE001
        log.warning('[pm] %s: %s', sport, e)

    out = []
    for g in games:
        if not (now - timedelta(hours=LOOKBACK_H) <= g['start'] <= now + timedelta(hours=HORIZON_H)):
            continue
        # Two markets placed on one game is a join that cannot be trusted.
        if len(kal.get(g['id'], [])) != 1 or len(pm.get(g['id'], [])) != 1:
            continue
        ml, toks = pm[g['id']][0]
        rate = pm_rate(ml)
        legs = []
        for sc, (tok, label) in toks.items():
            pay = {s: (1.0 if s == sc else 0.0) for s in scenarios}
            if 'tie' in scenarios:
                pay['tie'] = 0.5
            legs.append(Leg(venue=venues.POLYMARKET, ref=str(tok), market=ml['conditionId'],
                            side='YES', label=f'PM:{label}', pay=pay, taker=rate, maker=0.0))
        legs += kalshi_legs(src['kalshi'], mults.get(src['kalshi'], 1.0), kal[g['id']][0],
                            scenarios, tie_pays_half=src['tie'])
        title = f"{g['away'].get('displayName')} @ {g['home'].get('displayName')}"
        out.append(Game(sport=sport, title=title, start=g['start'], scenarios=scenarios, legs=legs))
    return out


# ---------------------------------------------------------------------------
# Soccer: Polymarket's 1X2 joined to Kalshi's through venues.py
# ---------------------------------------------------------------------------

def build_soccer(now: datetime, mults: dict) -> list:
    d0 = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    d1 = (now + timedelta(days=3)).strftime('%Y-%m-%d')
    events = []
    for offset in range(0, 2000, 100):
        try:
            page = _get(f'{GAMMA}/events', {'tag_id': SOCCER_TAG, 'closed': 'false', 'limit': 100,
                                            'offset': offset, 'end_date_min': d0, 'end_date_max': d1})
        except requests.RequestException as e:
            log.warning('[pm] soccer stopped at offset %d: %s', offset, e)
            break
        events.extend(page or [])
        if not page or len(page) < 100:
            break

    idx = venues.KalshiSoccerIndex(auto_refresh=False)
    idx.load(force=True)
    scenarios = ('home', 'draw', 'away')
    out = []
    for e in events:
        title = e.get('title') or ''
        if ' - ' in title:          # "More Markets", "Halftime Result", ...
            continue
        teams = venues.split_vs(title)
        start = _dt(e.get('startTime')) or _dt(e.get('endDate'))
        if not teams or start is None or e.get('ended'):
            continue
        if not (now - timedelta(hours=LOOKBACK_H) <= start <= now + timedelta(hours=HORIZON_H)):
            continue
        h, a = teams
        kf = idx.fixture(h, a, start)
        swapped = False
        if kf is None:
            kf = idx.fixture(a, h, start)
            swapped = kf is not None
        if kf is None or not all(s in kf.legs for s in scenarios):
            continue

        legs = []
        for m in e.get('markets') or []:
            oc = _pm_outcome(m.get('question', ''), h, a)
            if oc is None:
                continue
            if swapped and oc != 'draw':
                oc = 'away' if oc == 'home' else 'home'
            toks, labels = _json_array(m.get('clobTokenIds')), _json_array(m.get('outcomes'))
            if not toks or not labels or len(toks) != 2:
                continue
            rate = pm_rate(m)
            for tok, lbl in zip(toks, labels):
                side = 'YES' if str(lbl).strip().lower() == 'yes' else 'NO'
                yes = {s: (1.0 if s == oc else 0.0) for s in scenarios}
                pay = yes if side == 'YES' else {s: 1.0 - v for s, v in yes.items()}
                legs.append(Leg(venue=venues.POLYMARKET, ref=str(tok), market=m['conditionId'],
                                side=side, label=f'PM:{side} {oc}', pay=pay, taker=rate, maker=0.0))
        if len(legs) != 6 or len({(l.pay['home'], l.pay['draw'], l.pay['away']) for l in legs}) != 6:
            continue        # not exactly one YES/NO pair per outcome
        kmk = {s: {'ticker': kf.legs[s][0], 'yes_sub_title': kf.legs[s][1]} for s in scenarios}
        legs += kalshi_legs(kf.series, mults.get(kf.series, 1.0), kmk, scenarios,
                            tie_pays_half=False)
        name = f'{kf.home} vs {kf.away} [{kf.competition}]'
        out.append(Game(sport='soccer', title=name, start=start, scenarios=scenarios, legs=legs))
    return out


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

def load_books(legs: list) -> None:
    pm = [l for l in legs if l.venue == venues.POLYMARKET]
    toks = sorted({l.ref for l in pm})
    books = {}
    for i in range(0, len(toks), 400):
        try:
            for b in _post(f'{CLOB}/books', [{'token_id': t} for t in toks[i:i + 400]]) or []:
                books[str(b.get('asset_id'))] = b
        except requests.RequestException as e:
            log.warning('[pm] /books: %s', e)
    for l in pm:
        b = books.get(l.ref) or {}
        l.ladder = _clean_ladder([(float(x['price']), float(x['size'])) for x in b.get('asks') or []])
        bids = [(float(x['price']), float(x['size'])) for x in b.get('bids') or [] if float(x['size']) > 0]
        l.bid, l.bid_size = max(bids) if bids else (None, None)

    kal = [l for l in legs if l.venue == venues.KALSHI]
    tickers = sorted({l.ref for l in kal})
    obs = {}
    for i in range(0, len(tickers), 50):
        try:
            d = venues._paced_get('/markets/orderbooks', {'tickers': tickers[i:i + 50]})
            for ob in d.get('orderbooks') or []:
                obs[ob.get('ticker')] = ob.get('orderbook_fp') or {}
        except Exception as e:      # noqa: BLE001
            log.warning('[kalshi] orderbooks: %s', e)
    for l in kal:
        ob = obs.get(l.ref) or {}
        yes_bids = [(float(p), float(s)) for p, s in ob.get('yes_dollars') or []]
        no_bids = [(float(p), float(s)) for p, s in ob.get('no_dollars') or []]
        # Kalshi rests BIDS on both sides: buying YES lifts the NO bids at 1-p.
        mine, other = (yes_bids, no_bids) if l.side == 'YES' else (no_bids, yes_bids)
        l.ladder = _clean_ladder([(round(1.0 - p, 4), s) for p, s in other])
        good = [(p, s) for p, s in mine if s > 0]
        l.bid, l.bid_size = max(good) if good else (None, None)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def build_all(sports: list) -> list:
    now = datetime.now(timezone.utc)
    mults = kalshi_multipliers()
    games = []
    for s in sports:
        try:
            gs = build_soccer(now, mults) if s == 'soccer' else build_us(s, now, mults)
        except Exception as e:      # noqa: BLE001 - one sport failing is not the scan
            log.warning('[build] %s failed: %s', s, e)
            continue
        log.info('[build] %-6s %3d games on both venues', s, len(gs))
        games.extend(gs)
    for g in games:
        g.baskets = enumerate_baskets(g.legs, g.scenarios)
    return games


def scan_once(games: list, fh, cycle: int) -> dict:
    now = datetime.now(timezone.utc)
    load_books([l for g in games for l in g.legs])
    stats = {'games': 0, 'cross_gross_pos': 0, 'arbs_seen': 0, 'arbs_confirmed': 0}
    for g in games:
        r = evaluate(g)
        if r['best_cross'] is None and r['best_intra'] is None:
            continue
        stats['games'] += 1
        if r['best_cross'] and r['best_cross']['gross_pp'] > 0:
            stats['cross_gross_pos'] += 1
        if r['arbs']:
            stats['arbs_seen'] += 1
            # Re-read both venues' books for THIS game and price it again: a
            # crossing built from two reads seconds apart is not yet an arb.
            load_books(g.legs)
            again = evaluate(g)
            r['confirmed'] = again['arbs']
            if again['arbs']:
                stats['arbs_confirmed'] += 1
                for a in again['arbs']:
                    log.info('ARB %s %s | %s | %.0f sh, $%.2f, %+.2fpp', g.sport, g.title,
                             ' + '.join(f"{x['label']}@{x['ask']}" for x in a['legs']),
                             a['shares'], a['profit'], a['profit_pp'])
        fh.write(json.dumps({'t': now.isoformat(timespec='seconds'), 'cycle': cycle,
                             'sport': g.sport, 'game': g.title,
                             'start': g.start.isoformat() if g.start else None,
                             'state': g.state(now), **r,
                             # Every leg's top of book, so execution can be
                             # replayed later (maker_window_sim.py).
                             'scenarios': list(g.scenarios),
                             'tob': [_tob(l, g.scenarios) for l in g.legs]}) + '\n')
    fh.flush()
    return stats


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _q(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float('nan')


def summarize(paths) -> None:
    paths = paths if isinstance(paths, (list, tuple)) else [paths]
    rows = []
    for i, p in enumerate(paths):
        for x in p.open():
            r = json.loads(x)
            r['cycle'] = (i, r['cycle'])
            rows.append(r)
    path = paths[-1]
    if not rows:
        print('no rows')
        return
    cycles = len({r['cycle'] for r in rows})
    print(f'\n{path.name}: {len(rows)} game-snapshots, {cycles} cycles, '
          f"{rows[0]['t']} → {rows[-1]['t']}\n")
    hdr = (f"{'sport':7s}{'state':6s}{'games':>6s}{'snaps':>7s}  {'cross gross pp: med':>20s}"
           f"{'p90':>7s}{'max':>7s}  {'net>0':>6s}{'arb seen':>9s}{'confirmed':>10s}"
           f"  {'maker med':>10s}{'>0':>6s}")
    print(hdr)
    print('-' * len(hdr))
    keys = sorted({(r['sport'], r['state']) for r in rows})
    for sport, state in keys:
        rs = [r for r in rows if r['sport'] == sport and r['state'] == state]
        g = [r['best_cross']['gross_pp'] for r in rs if r.get('best_cross')]
        n = [r['best_cross']['net_pp'] for r in rs if r.get('best_cross')]
        mk = [r['maker_pp'] for r in rs if r.get('maker_pp') is not None]
        seen = sum(1 for r in rs if r.get('arbs') and any(a['cross'] for a in r['arbs']))
        conf = sum(1 for r in rs if r.get('confirmed') and any(a['cross'] for a in r['confirmed']))
        print(f"{sport:7s}{state:6s}{len({r['game'] for r in rs}):6d}{len(rs):7d}  "
              f"{_q(g, .5):20.2f}{_q(g, .9):7.2f}{max(g) if g else float('nan'):7.2f}  "
              f"{sum(1 for x in n if x > 0):6d}{seen:9d}{conf:10d}  "
              f"{_q(mk, .5):10.2f}{sum(1 for x in mk if x > 0):6d}")

    arbs = [(r, a) for r in rows for a in (r.get('confirmed') or []) if a['cross']]
    print(f'\nConfirmed cross-venue arbs (after a second read of both books): {len(arbs)}')
    by_game = {}
    for r, a in arbs:
        k = (r['sport'], r['game'])
        if k not in by_game or a['profit'] > by_game[k][1]['profit']:
            by_game[k] = (r, a)
    for (sport, game), (r, a) in sorted(by_game.items(), key=lambda x: -x[1][1]['profit']):
        n_snaps = sum(1 for rr, aa in arbs if rr['game'] == game)
        print(f"  {sport:6s} {r['state']:5s} {game[:58]:58s} best ${a['profit']:.2f} on "
              f"{a['shares']:.0f} sh ({a['profit_pp']:+.2f}pp) · seen in {n_snaps} snap(s)")
        legs = ' + '.join('%s@%s' % (x['label'], x['ask']) for x in a['legs'])
        print(f"         {legs} · fees ${a['fees']:.2f} · {r['t']}")
    intra = [(r, a) for r in rows for a in (r.get('confirmed') or []) if not a['cross']]
    if intra:
        print(f'\nIntra-venue arbs confirmed: {len(intra)}')
    unconf = sum(1 for r in rows if r.get('arbs') and not r.get('confirmed'))
    print(f'Crossings that did NOT survive the second read: {unconf}')

    closest = sorted((r for r in rows if r.get('best_cross')),
                     key=lambda r: -r['best_cross']['net_pp'])[:10]
    print('\nClosest cross-venue baskets, net of fees at top of book:')
    for r in closest:
        b = r['best_cross']
        print(f"  {b['net_pp']:+6.2f}pp net ({b['gross_pp']:+.2f} gross)  {r['sport']:6s} "
              f"{r['state']:5s} {r['game'][:48]:48s} {b['legs']}")
    print()


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sports', default=','.join(ALL_SPORTS))
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--minutes', type=float, default=60.0)
    ap.add_argument('--interval', type=float, default=60.0)
    ap.add_argument('--reindex-min', type=float, default=15.0)
    ap.add_argument('--out', type=Path, default=None)
    ap.add_argument('--summary', type=Path, nargs='+', default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    for noisy in ('urllib3', 'requests'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.summary:
        summarize(args.summary)
        return

    sports = [s.strip() for s in args.sports.split(',') if s.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or OUT_DIR / f"scan_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M')}.jsonl"
    end = time.time() + (0 if args.once else args.minutes * 60)
    games, built, cycle = [], 0.0, 0
    with out.open('a') as fh:
        while True:
            if not games or time.time() - built > args.reindex_min * 60:
                games, built = build_all(sports), time.time()
            t0 = time.time()
            st = scan_once(games, fh, cycle)
            log.info('[cycle %d] %d games priced · cross gross>0 on %d · arbs seen %d, confirmed %d · %.0fs',
                     cycle, st['games'], st['cross_gross_pos'], st['arbs_seen'], st['arbs_confirmed'],
                     time.time() - t0)
            cycle += 1
            if time.time() >= end:
                break
            time.sleep(max(0.0, args.interval - (time.time() - t0)))
    summarize(out)


if __name__ == '__main__':
    main()
