"""Team watch list: is the market slow on the clubs running above their prices?

The operator's thesis (2026-10-11): when a team improves, bookmakers and the
market are slow to move off its past odds and stats. A team having an unusual
season can therefore be backed THAT season, and dropped when it ends. A
profitable season is not "luck" just because the next one is different.

The test is closing line value (CLV), not P&L. CLV answers in tens of games,
where yield would need thousands. Two lists, each against a control of every
other club in the same leagues and on the same dates:

| list | membership | evidence from |
|---|---|---|
| `frozen` | the clubs with the best back-every-match returns, fixed on `frozen_at` (team_watch.json) | games AFTER `frozen_at` only |
| `dynamic` | in-season, from game 6: season-to-date points >= +0.30/game above what the closing prices expected | every game where the rule was on, computed from earlier games only |

Two CLV sources, both free and both already recorded:

| | entry | close | coverage |
|---|---|---|---|
| Football-Data | the pre-close snapshot (Friday afternoon for weekend games, Tuesday for midweek): best bookmaker odds (Max) and the average | Betfair Exchange close, power de-vigged; Avg close where BFE is missing | our 22 leagues, played games |
| Polymarket | the first tradable quote `venue_open_recorder` saw (db/071), ask + 5% taker fee | the last pre-KO mid | listed fixtures, from 2026-10-10 |

    move_pp = (p_close - p_entry) x 100   positive = the market moved TOWARD the team
    clv     = entry_odds x p_close - 1    the entry price valued at the fair close

`move_pp` is margin-free and is the direct reading of "the market is slow":
if the thesis holds, it is positive on the watched clubs and larger than the
control. `clv` adds the price you could actually get.

Already measured (memory: finding-back-a-team-roi): at Pinnacle, the OPEN
over-credits recent form and the close takes it back. That was Pinnacle's open.
This list measures the bookmakers' Friday price and Polymarket.

    python team_watch.py --build            # freeze the list (refuses to overwrite)
    python team_watch.py --report           # refresh Football-Data, read Polymarket, print
    python team_watch.py --report --save    # ... and write reports/team_watch_<date>.md
    python team_watch.py --report --no-pm   # Football-Data only (no DATABASE_URL needed)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pinnacle_audit                                               # noqa: E402

WATCH_FILE = os.path.join(HERE, 'team_watch.json')
REPORTS = os.path.join(HERE, '..', 'reports')
CACHE = pinnacle_audit.CACHE

LEAGUE = {'E0': 'Premier League', 'E1': 'Championship', 'E2': 'League One', 'E3': 'League Two',
          'EC': 'National League', 'SC0': 'Scottish Prem', 'SC1': 'Scottish Champ', 'SC2': 'Scottish L1',
          'SC3': 'Scottish L2', 'D1': 'Bundesliga', 'D2': '2. Bundesliga', 'I1': 'Serie A', 'I2': 'Serie B',
          'SP1': 'La Liga', 'SP2': 'LaLiga 2', 'F1': 'Ligue 1', 'F2': 'Ligue 2', 'N1': 'Eredivisie',
          'B1': 'Belgian Pro', 'P1': 'Primeira Liga', 'T1': 'Super Lig', 'G1': 'Greek SL'}
# Polymarket's `competition` for each league, read off venue_market_quotes on
# 2026-10-11. Exact equality only: "Serie A" alone would also be Brazil's.
# Serie A is the code "sea". The lower Scottish divisions are not listed.
PM_COMPETITION = {'Premier League': 'E0', 'EFL Championship': 'E1', 'League One': 'E2', 'League Two': 'E3',
                  'National League': 'EC', 'Scottish Premiership': 'SC0', 'Bundesliga': 'D1',
                  'Bundesliga 2': 'D2', 'sea': 'I1', 'Serie B': 'I2', 'La Liga': 'SP1', 'La Liga 2': 'SP2',
                  'Ligue 1': 'F1', 'Ligue 2': 'F2', 'Eredivisie': 'N1', 'Belgium Pro League': 'B1',
                  'Primeira Liga': 'P1', 'Süper Lig': 'T1', 'Greece Super League': 'G1'}

# The dynamic rule. The same detector tested on 168k team-games on 2026-10-11:
# against the next game's CLOSE it carried nothing. What is new here is the
# entry point: a Friday bookmaker price and Polymarket's open, not the close.
MIN_GAMES = 5            # season games already played before the rule can fire
THRESH = 0.30            # points per game above what the closing prices expected

# The frozen list, as shown to the operator on 2026-10-11.
FROZEN_WINDOWS = (('12m', 365, 25, 15), ('season_prev', None, 30, 15), ('season_now', None, 5, 12))

OPEN_MAX_SPREAD = 0.10   # venue_open_recorder's bar for a tradable quote
PM_TAKER_FEE = 0.05      # soccer, from 2026-07/08 (memory: pm_fees): rate x p x (1 - p) per share
BOOK_OK = (0.97, 1.08)   # a 1X2 book whose overround is outside this is not a price


# ── Football-Data ────────────────────────────────────────────────────────────

def current_season(today: date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def refresh(today: date) -> None:
    """Re-download this season and last; older files stay cached."""
    pinnacle_audit.download(first=current_season(today) - 1)


_ODDS = {'pre_bfe': 'BFE', 'pre_avg': 'Avg', 'pre_max': 'Max',
         'close_bfe': 'BFEC', 'close_avg': 'AvgC', 'close_max': 'MaxC'}


def load_fd(seasons) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(os.path.join(CACHE, '*.csv'))):
        code, tag = re.match(r'.*/(\w+)_(\d{4})\.csv$', f).groups()
        season = 2000 + int(tag[:2])
        if season not in seasons or code not in LEAGUE:
            continue
        d = pd.read_csv(f, encoding='latin1', on_bad_lines='skip')
        keep = ['Date', 'HomeTeam', 'AwayTeam', 'FTR'] + [p + s for p in _ODDS.values() for s in 'HDA']
        d = d[[c for c in keep if c in d.columns]].copy()
        d['code'], d['season'] = code, season
        frames.append(d)
    d = pd.concat(frames, ignore_index=True).dropna(subset=['HomeTeam', 'AwayTeam', 'FTR'])
    d['date'] = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce', format='mixed')
    d = d.dropna(subset=['date'])
    for p in _ODDS.values():
        for s in 'HDA':
            c = p + s
            d[c] = pd.to_numeric(d[c], errors='coerce') if c in d else np.nan
    return d.sort_values('date').reset_index(drop=True)


def power_devig(oh, od, oa):
    """Power de-vig of a 1X2 book: p_i = (1/o_i)^k with k such that sum = 1.

    Proportional de-vig overstates longshots and manufactures the very bias
    under test (factory v2, 2026-10-10). Rows whose book is not a price
    (missing leg, overround outside BOOK_OK) come back NaN.
    """
    q = np.vstack([1 / np.asarray(oh, float), 1 / np.asarray(od, float), 1 / np.asarray(oa, float)]).T
    s0 = q.sum(1)
    bad = ~np.isfinite(s0) | (s0 < BOOK_OK[0]) | (s0 > BOOK_OK[1])
    q = np.where(bad[:, None], 0.5, q)
    k = np.ones(len(q))
    for _ in range(50):
        qk = q ** k[:, None]
        k = k - (qk.sum(1) - 1) / (qk * np.log(q)).sum(1)
    p = q ** k[:, None]
    p[bad] = np.nan
    return p


def _fair(d: pd.DataFrame, first: str, fallback: str) -> np.ndarray:
    a = power_devig(d[first + 'H'], d[first + 'D'], d[first + 'A'])
    b = power_devig(d[fallback + 'H'], d[fallback + 'D'], d[fallback + 'A'])
    return np.where(np.isnan(a), b, a)


def team_games(d: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match, with the in-season detector computed from
    EARLIER games only (`resid_before`) and including this one (`resid_after`)."""
    pre = _fair(d, 'BFE', 'Avg')
    close = _fair(d, 'BFEC', 'AvgC')
    rows = []
    for side, opp, res in (('H', 'A', 'H'), ('A', 'H', 'A')):
        j = 0 if side == 'H' else 2
        rows.append(pd.DataFrame({
            'code': d.code, 'season': d.season, 'date': d.date,
            'team': d.HomeTeam if side == 'H' else d.AwayTeam,
            'opp': d.AwayTeam if side == 'H' else d.HomeTeam, 'venue': side,
            'won': (d.FTR == res).astype(int), 'draw': (d.FTR == 'D').astype(int),
            'p_pre': pre[:, j], 'p_close': close[:, j], 'pd_close': close[:, 1],
            'entry_best': d['Max' + side], 'entry_avg': d['Avg' + side], 'close_avg': d['AvgC' + side],
        }))
    t = pd.concat(rows, ignore_index=True).sort_values(['code', 'season', 'team', 'date']).reset_index(drop=True)
    t['pts'] = 3 * t.won + t.draw
    t['resid'] = t.pts - (3 * t.p_close + t.pd_close)
    g = t.groupby(['code', 'season', 'team'])['resid']
    t['gnum'] = t.groupby(['code', 'season', 'team']).cumcount()
    t['resid_after'] = g.transform(lambda s: s.expanding().mean())
    t['resid_before'] = g.transform(lambda s: s.shift(1).expanding().mean())
    t['dynamic_on'] = (t.gnum >= MIN_GAMES) & (t.resid_before >= THRESH)
    t['move_pp'] = (t.p_close - t.p_pre) * 100
    t['clv_best'] = t.entry_best * t.p_close - 1
    t['clv_avg'] = t.entry_avg * t.p_close - 1
    t['ret_best'] = np.where(t.won == 1, t.entry_best - 1, -1.0)
    t['ret_close_avg'] = np.where(t.won == 1, t.close_avg - 1, -1.0)
    return t


# ── the frozen list ──────────────────────────────────────────────────────────

def build_frozen(t: pd.DataFrame, today: date) -> dict:
    """Keyed on the CLUB, not (league, club): Hull, Lincoln, Schalke and Le Mans
    changed division in the summer, and a (league, club) key would watch the
    division they left. Football-Data names are unique across our 22 leagues
    (checked 2026-10-11: the only names in two leagues are English clubs moving
    between League Two and the National League)."""
    now_s = current_season(today)
    latest = t.sort_values('date').groupby('team').code.last()
    out = {}
    for name, days, min_n, top in FROZEN_WINDOWS:
        if name == '12m':
            w = t[t.date >= pd.Timestamp(today - timedelta(days=days))]
        else:
            w = t[t.season == (now_s - 1 if name == 'season_prev' else now_s)]
        w = w.dropna(subset=['close_avg'])
        g = (w.groupby('team')
              .agg(n=('won', 'size'), roi=('ret_close_avg', 'mean'), above=('resid', 'mean'))
              .reset_index())
        g = g[g.n >= min_n].sort_values('roi', ascending=False).head(top)
        for r in g.itertuples():
            code = latest[r.team]
            e = out.setdefault(r.team, {'code': code, 'team': r.team, 'league': LEAGUE[code], 'why': []})
            e['why'].append({'window': name, 'games': int(r.n), 'roi_avg_close': round(float(r.roi), 4),
                             'pts_above_price': round(float(r.above), 3)})
    return {
        'frozen_at': today.isoformat(),
        'data_through': t.date.max().date().isoformat(),
        'rule': ('back-every-match ROI at the average bookmaker close: top 15 over the last 365 days '
                 '(>=25 games), top 15 of the previous season (>=30), top 12 of this season (>=5)'),
        'dynamic_rule': {'min_games_played': MIN_GAMES, 'pts_per_game_above_close_price': THRESH},
        'teams': sorted(out.values(), key=lambda e: (e['code'], e['team'])),
    }


def load_watch() -> dict:
    with open(WATCH_FILE) as f:
        return json.load(f)


# ── Polymarket ───────────────────────────────────────────────────────────────

def load_pm() -> pd.DataFrame:
    from dotenv import load_dotenv
    import db_txn
    load_dotenv(os.path.join(HERE, '..', 'ingest', '.env'))
    # Supavisor's transaction port: a short job must not hold a session slot.
    conn = db_txn.connect(os.environ['DATABASE_URL'].replace(':5432/', ':6543/'))
    try:
        return pd.read_sql("""
            SELECT market_key, competition, event_key, event_title, home, away, outcome, kickoff,
                   open_at, open_bid, open_ask, last_at, last_bid, last_ask
              FROM venue_market_quotes
             WHERE venue = 'polymarket' AND sport = 'soccer' AND family = 'moneyline'
               AND competition = ANY(%s)""", conn, params=(list(PM_COMPETITION),))
    finally:
        conn.close()


# Polymarket name -> Football-Data's exact spelling, for clubs the scorer cannot
# reach or must not guess. Every one was read off venue_market_quotes and the
# 2026-27 CSVs on 2026-10-11. Several are ties by design: "Dundee FC" scores 1.0
# against Dundee AND Dundee United, "Paris FC" against Paris FC AND Paris SG,
# "Cercle Brugge" against Club Brugge ("club" is noise). A tie fails closed, so
# without this table those clubs would never be measured.
PM_TO_FD = {
    'Dundee FC': 'Dundee', 'Paris FC': 'Paris FC', 'Cercle Brugge': 'Cercle Brugge',
    'Stade Rennais FC 1901': 'Rennes', 'Sheffield United FC': 'Sheffield United',
    'Olympiacos Piraeus': 'Olympiakos', 'Karlsruher SC': 'Karlsruhe', 'AS Saint-Étienne': 'St Etienne',
    'Göztepe SK': 'Goztep', 'Rams Başakşehir FK': 'Buyuksehyr', 'Peterborough United FC': 'Peterboro',
    'Bristol Rovers FC': 'Bristol Rvs', 'NEC': 'Nijmegen', 'St. Truidense VV': 'St Truiden',
    'Union Saint-Gilloise': 'St. Gilloise', 'Celta Fortuna': 'Celta B', 'APO Levadiakos FC': 'Levadeiakos',
    'Volos NPS': 'Volos NFC', 'Panaitolikos Agrinio': 'Panetolikos', 'Borussia Mönchengladbach': "M'gladbach",
    'RC Deportivo A Coruña': 'La Coruna', 'Real Racing Club': 'Santander', 'Vitória SC': 'Guimaraes',
    'RCD Espanyol de Barcelona': 'Espanol', 'Rayo Vallecano de Madrid': 'Vallecano',
}


class Resolver:
    """Polymarket's club name -> Football-Data's, inside one league.

    Both sides of the fixture must resolve, to two different clubs of that
    league, or the fixture is dropped: a side error inverts what is measured.
    Order: PM_TO_FD (exact); then agent/team_aliases.json, whose targets are
    our DB's canonical names and NOT always Football-Data's ("Leipzig" for
    "RB Leipzig", "Athletic Bilbao" for "Ath Bilbao"), so the target is itself
    scored against the league; then fixture_match's strict scorer on the PM
    name. A score needs a unique best at >= 0.60.
    """

    def __init__(self, t: pd.DataFrame, season: int):
        import fixture_match as fm
        self.fm = fm
        self.clubs = {c: sorted(g.team.unique()) for c, g in t[t.season == season].groupby('code')}
        with open(os.path.join(HERE, 'team_aliases.json')) as f:
            self.alias = {fm._norm_key(k): v for k, v in json.load(f).items()}
        self.local = {fm._norm_key(k): v for k, v in PM_TO_FD.items()}

    def _score(self, name: str, clubs):
        scored = sorted(((self.fm.team_score(name, c), c) for c in clubs), reverse=True)
        if not scored or scored[0][0] < self.fm.MIN_SIDE_SCORE:
            return None
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return None
        return scored[0][1]

    def club(self, name: str, code: str):
        clubs = self.clubs.get(code, [])
        key = self.fm._norm_key(name)
        if key in self.local:
            return self.local[key] if self.local[key] in clubs else None
        a = self.alias.get(key)
        if a is not None:
            if a.startswith('__'):                  # an explicit "not one of ours"
                return None
            return a if a in clubs else self._score(a, clubs)
        return self._score(name, clubs)

    def fixture(self, home: str, away: str, code: str):
        h, a = self.club(home, code), self.club(away, code)
        return (h, a) if h and a and h != a else (None, None)


def pm_rows(pm: pd.DataFrame, t: pd.DataFrame, today: date) -> pd.DataFrame:
    """One row per (fixture, team) Yes token, with the club resolved and the
    entry/close metrics. Draw tokens are dropped: the thesis is about a team."""
    if pm.empty:
        return pm
    res = Resolver(t, current_season(today))
    pm = pm[~pm.outcome.str.contains(r'\bdraw\b', case=False, na=False)].copy()
    pm['code'] = pm.competition.map(PM_COMPETITION)
    fx = {}
    for r in pm[['event_key', 'home', 'away', 'code']].drop_duplicates('event_key').itertuples():
        fx[r.event_key] = res.fixture(r.home, r.away, r.code)
    pm['home_fd'] = pm.event_key.map(lambda k: fx[k][0])
    pm['away_fd'] = pm.event_key.map(lambda k: fx[k][1])
    pm = pm.dropna(subset=['home_fd', 'away_fd'])
    side = np.where(pm.outcome == pm.home, 'H', np.where(pm.outcome == pm.away, 'A', ''))
    pm['team'] = np.where(side == 'H', pm.home_fd, np.where(side == 'A', pm.away_fd, None))
    pm = pm[pm.team.notna()].copy()
    for c in ('open_bid', 'open_ask', 'last_bid', 'last_ask'):
        pm[c] = pd.to_numeric(pm[c], errors='coerce').astype(float)
    pm['kickoff'] = pd.to_datetime(pm.kickoff, utc=True)
    pm['open_at'] = pd.to_datetime(pm.open_at, utc=True)
    pm['hours_before'] = (pm.kickoff - pm.open_at).dt.total_seconds() / 3600
    pm['open_mid'] = (pm.open_bid + pm.open_ask) / 2
    pm['last_mid'] = (pm.last_bid + pm.last_ask) / 2
    cost = pm.open_ask * (1 + PM_TAKER_FEE * (1 - pm.open_ask))
    pm['move_pp'] = (pm.last_mid - pm.open_mid) * 100
    pm['clv_net'] = pm.last_mid / cost - 1
    pm['settled'] = pm.kickoff < pd.Timestamp.now(tz='UTC')
    return pm


def dynamic_status(t: pd.DataFrame, team: str, code: str, before: pd.Timestamp) -> bool:
    """Was `team` on the dynamic list going into a game at `before`?
    Uses only the Football-Data games played before it."""
    h = t[(t.team == team) & (t.code == code) & (t.date < before.tz_localize(None).normalize())]
    h = h[h.season == h.season.max()] if len(h) else h
    if len(h) < MIN_GAMES:
        return False
    return bool(h.resid_after.iloc[-1] >= THRESH)


# ── statistics ───────────────────────────────────────────────────────────────

_RNG = np.random.default_rng(20261011)


def cluster_ci(values, clusters, B: int = 2000):
    """Mean with a 95% bootstrap CI that resamples whole clusters (team or
    fixture): games of one club are not independent draws."""
    df = pd.DataFrame({'v': np.asarray(values, float), 'c': np.asarray(clusters)}).dropna()
    if df.empty:
        return np.nan, np.nan, np.nan, 0
    s = df.groupby('c')['v'].agg(['sum', 'count']).values
    m = s[:, 0].sum() / s[:, 1].sum()
    if len(s) < 2:
        return m, np.nan, np.nan, int(s[:, 1].sum())
    idx = _RNG.integers(0, len(s), (B, len(s)))
    boots = s[idx, 0].sum(1) / s[idx, 1].sum(1)
    return m, np.percentile(boots, 2.5), np.percentile(boots, 97.5), int(s[:, 1].sum())


def fmt(m, lo, hi, n, pct=True, unit=''):
    if n == 0 or np.isnan(m):
        return '—'
    k = 100 if pct else 1
    ci = '' if np.isnan(lo) else f' [{lo * k:+.2f}, {hi * k:+.2f}]'
    return f'{m * k:+.2f}{unit}{ci}'


# ── report ───────────────────────────────────────────────────────────────────

def report(t: pd.DataFrame, watch: dict, pm: pd.DataFrame | None, today: date) -> list[str]:
    L = []
    frozen_at = pd.Timestamp(watch['frozen_at'])
    frozen = {e['team'] for e in watch['teams']}
    t_all = t
    t = t.assign(frozen=t.team.isin(frozen), cl=t.code + '|' + t.team)
    t = t.dropna(subset=['p_pre', 'p_close'])

    L.append(f'# Team watch list — {today.isoformat()}')
    L.append('')
    L.append(f'Football-Data games through {t.date.max().date()}. Frozen list: {len(frozen)} clubs, '
             f'frozen {watch["frozen_at"]}. Dynamic rule: from game {MIN_GAMES + 1}, '
             f'season-to-date >= +{THRESH:.2f} pts/game above the closing prices.')
    L.append('')
    L.append('`move` = market move from the entry price to the close, in pp of win probability '
             '(+ = toward the team). `CLV` = entry odds valued at the fair close. Brackets: 95% CI, '
             'resampled by club.')

    def block(title, sel, ctrl, note=''):
        L.append('')
        L.append(f'## {title}')
        if note:
            L.append(note)
        L.append('')
        L.append('| group | games | clubs | move (pp) | CLV best Friday odds (%) | CLV average odds (%) | ROI best odds (%) |')
        L.append('|---|--:|--:|--:|--:|--:|--:|')
        for name, s in (('watched', sel), ('control (everyone else)', ctrl)):
            mv = cluster_ci(s.move_pp / 100, s.cl)
            cb = cluster_ci(s.clv_best, s.cl)
            ca = cluster_ci(s.clv_avg, s.cl)
            rr = cluster_ci(s.ret_best, s.cl)
            L.append(f'| {name} | {len(s)} | {s.cl.nunique()} | {fmt(*mv)} | {fmt(*cb)} | {fmt(*ca)} | {fmt(*rr)} |')

    # 1. Frozen list, forward only
    fwd = t[t.date > frozen_at]
    block('Frozen list, games after it was frozen (the test)', fwd[fwd.frozen], fwd[~fwd.frozen],
          'Empty until Football-Data publishes games played after the freeze.' if fwd.frozen.sum() == 0 else '')

    # 2. Dynamic rule, reconstructed: every game where it was on, from earlier games only
    now_s = current_season(today)
    for season in (now_s - 1, now_s):
        s = t[t.season == season]
        block(f'Dynamic rule, season {season}-{(season + 1) % 100:02d} (reconstructed, no lookahead)',
              s[s.dynamic_on], s[~s.dynamic_on & (s.gnum >= MIN_GAMES)],
              f'Control = every other club-game from game {MIN_GAMES + 1}. The threshold was not fitted on this '
              'season, but this IS the population the detector was tested on (against the close), so it is a '
              'reading, not a confirmation.')

    # 3. Who is on the dynamic list right now
    cur = t_all[t_all.season == now_s].sort_values('date').groupby(['code', 'team']).tail(1)
    on = cur[(cur.gnum + 1 >= MIN_GAMES) & (cur.resid_after >= THRESH)].sort_values('resid_after', ascending=False)
    L.append('')
    L.append(f'## On the dynamic list now ({len(on)} clubs)')
    L.append('')
    L.append('| club | league | games | pts/game above price | on frozen list |')
    L.append('|---|---|--:|--:|:-:|')
    for r in on.itertuples():
        L.append(f'| {r.team} | {LEAGUE[r.code]} | {r.gnum + 1} | {r.resid_after:+.2f} | '
                 f'{"yes" if r.team in frozen else ""} |')

    # 4. Polymarket
    if pm is None:
        L.append('')
        L.append('## Polymarket\n\nSkipped (--no-pm).')
        return L
    L.append('')
    L.append('## Polymarket: first tradable quote -> last quote before kick-off')
    L.append('')
    if pm.empty:
        L.append('No Polymarket rows resolved.')
        return L
    pm = pm.assign(frozen=pm.team.isin(frozen))
    pm['dynamic'] = [dynamic_status(t_all, tm, c, k) for tm, c, k in zip(pm.team, pm.code, pm.kickoff)]
    pm['cl'] = pm.event_key
    done = pm[pm.settled & pm.open_ask.notna() & pm.last_mid.notna()
              & ((pm.last_ask - pm.last_bid) <= OPEN_MAX_SPREAD)]
    done = done[done.kickoff >= pd.Timestamp(frozen_at, tz='UTC')]
    L.append(f'{pm.event_key.nunique()} fixtures resolved on both sides across our leagues; '
             f'{done.event_key.nunique()} have kicked off since the freeze. Entry = the first quote '
             f'with both sides and spread <= 0.10, paying the {PM_TAKER_FEE:.0%} taker fee; '
             f'median {done.hours_before.median():.0f}h before kick-off. ⚠️ Fixtures listed before '
             '2026-10-10 enter at the recorder\'s first look, not at their true opening.')
    L.append('')
    L.append('| group | tokens | fixtures | move (pp) | CLV net of fee (%) |')
    L.append('|---|--:|--:|--:|--:|')
    for name, s in (('frozen list', done[done.frozen]), ('dynamic list', done[done.dynamic]),
                    ('control (neither)', done[~done.frozen & ~done.dynamic])):
        L.append(f'| {name} | {len(s)} | {s.event_key.nunique()} | {fmt(*cluster_ci(s.move_pp / 100, s.cl))} | '
                 f'{fmt(*cluster_ci(s.clv_net, s.cl))} |')

    up = pm[~pm.settled & (pm.frozen | pm.dynamic)].sort_values('kickoff')
    L.append('')
    L.append(f'### Upcoming on Polymarket for watched clubs ({len(up)})')
    L.append('')
    L.append('| kick-off (UTC) | club | fixture | list | first odds | now odds | move (pp) |')
    L.append('|---|---|---|---|--:|--:|--:|')
    for r in up.itertuples():
        lst = '+'.join(x for x, f in (('frozen', r.frozen), ('dynamic', r.dynamic)) if f)
        # A quote wider than the open's own bar is a parked order, not a price:
        # "1.61 for Eibar away at Cordoba" was one, on an empty book.
        live = (r.last_ask - r.last_bid) <= OPEN_MAX_SPREAD if np.isfinite(r.last_ask - r.last_bid) else False
        o1 = f'{1 / r.open_ask:.2f}' if np.isfinite(r.open_ask) and r.open_ask > 0 else '—'
        o2 = f'{1 / r.last_ask:.2f}' if live and r.last_ask > 0 else '—'
        mv = f'{r.move_pp:+.1f}' if live and np.isfinite(r.move_pp) else '—'
        L.append(f'| {r.kickoff:%a %d %b %H:%M} | {r.team} | {r.event_title} | {lst} | {o1} | {o2} | {mv} |')
    return L


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', action='store_true', help='freeze the watch list into team_watch.json')
    ap.add_argument('--force', action='store_true', help='with --build: overwrite an existing frozen list')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--save', action='store_true', help='with --report: write reports/team_watch_<date>.md')
    ap.add_argument('--no-pm', action='store_true', help='skip Polymarket (no database)')
    ap.add_argument('--no-refresh', action='store_true', help='use the cached Football-Data files')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    if not (a.build or a.report):
        ap.print_help()
        return
    if not a.no_refresh:
        refresh(today)
    s = current_season(today)
    t = team_games(load_fd({s - 1, s}))

    if a.build:
        if os.path.exists(WATCH_FILE) and not a.force:
            sys.exit(f'{WATCH_FILE} exists and is frozen; --force replaces it, and restarts the forward test.')
        w = build_frozen(t, today)
        with open(WATCH_FILE, 'w') as f:
            json.dump(w, f, indent=1, ensure_ascii=False)
            f.write('\n')
        print(f'frozen {len(w["teams"])} clubs on {w["frozen_at"]} (data through {w["data_through"]}) -> {WATCH_FILE}')

    if a.report:
        watch = load_watch()
        pm = None if a.no_pm else pm_rows(load_pm(), t, today)
        lines = report(t, watch, pm, today)
        text = '\n'.join(lines) + '\n'
        print(text)
        if a.save:
            path = os.path.join(REPORTS, f'team_watch_{today.isoformat()}.md')
            with open(path, 'w') as f:
                f.write(text)
            print(f'-> {path}')


if __name__ == '__main__':
    main()
