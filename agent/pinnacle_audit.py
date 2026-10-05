"""Measure Pinnacle's closing line instead of using it as the ruler.

Reads Football-Data's raw CSVs (22 leagues, 2012-13 onward: Pinnacle close
on every match; market-max close, O/U and AH close from 2019-20) and asks
where the close is mis-calibrated and whether any of it pays AT THE CLOSE.

    python pinnacle_audit.py --calibration   # 1X2 by price band, train/test
    python pinnacle_audit.py --scan          # side x band x {tier, phase, promoted, move, ...}, BH on train
    python pinnacle_audit.py --clubs         # the rolling "clubs that beat the close as heavy favourites" strategy
    python pinnacle_audit.py --picks 2026    # the clubs that rule selects for a season, from earlier seasons only

Train = seasons 2012-2019, test = 2020 onward, unless stated. `p` is the
multiplicative de-vig of Pinnacle's close, so a calibration gap is measured
against that; ROI is always at the real quoted odds, which is what a bettor
gets. Report: reports/pinnacle_audit_2026-09-29.md.
"""
import argparse, glob, os, re, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, '.cache', 'pinnacle_audit')
TIER = {'E0': 1, 'E1': 2, 'E2': 3, 'E3': 4, 'EC': 5, 'SC0': 1, 'SC1': 2, 'SC2': 3, 'SC3': 4,
        'D1': 1, 'D2': 2, 'I1': 1, 'I2': 2, 'SP1': 1, 'SP2': 2, 'F1': 1, 'F2': 2,
        'N1': 1, 'B1': 1, 'P1': 1, 'T1': 1, 'G1': 1}
KEEP = ['Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'FTR', 'PSH', 'PSD', 'PSA',
        'PSCH', 'PSCD', 'PSCA', 'MaxCH', 'MaxCD', 'MaxCA', 'PC>2.5', 'PC<2.5', 'MaxC>2.5', 'MaxC<2.5']
TEST_FROM = 2020

# The strategy, fixed before the season it trades (see --clubs).
CLUB_MAX_ODDS = 1.6     # heavy favourites only
CLUB_MIN_N = 40         # at least this many earlier heavy-favourite games
CLUB_SHRINK = 60        # residual shrunk by n/(n+60)
CLUB_THRESHOLD = 0.04   # shrunk (hit - de-vigged p) >= 4pp


def download(first=2012, last=None):
    import requests
    os.makedirs(CACHE, exist_ok=True)
    last = last or pd.Timestamp.now('UTC').year
    for y in range(first, last + 1):
        tag = f'{y % 100:02d}{(y + 1) % 100:02d}'
        for code in TIER:
            path = os.path.join(CACHE, f'{code}_{tag}.csv')
            if os.path.exists(path) and os.path.getsize(path) > 1000 and y < last - 1:
                continue
            r = requests.get(f'https://www.football-data.co.uk/mmz4281/{tag}/{code}.csv', timeout=30)
            if r.ok and len(r.content) > 1000:
                open(path, 'wb').write(r.content)


def load():
    frames = []
    for f in sorted(glob.glob(os.path.join(CACHE, '*.csv'))):
        code, tag = re.match(r'.*/(\w+)_(\d{4})\.csv$', f).groups()
        d = pd.read_csv(f, encoding='latin1', on_bad_lines='skip')
        d = d[[c for c in KEEP if c in d.columns]].copy()
        d['code'], d['season'], d['tier'] = code, 2000 + int(tag[:2]), TIER[code]
        frames.append(d)
    d = pd.concat(frames, ignore_index=True).dropna(subset=['HomeTeam', 'FTR', 'PSCH', 'PSCD', 'PSCA'])
    d['date'] = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce', format='mixed')
    d = d.dropna(subset=['date'])
    for c in d.columns:
        if c not in ('Date', 'HomeTeam', 'AwayTeam', 'FTR', 'code', 'date'):
            d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d[(d.PSCH > 1) & (d.PSCD > 1) & (d.PSCA > 1)].sort_values('date').reset_index(drop=True)
    return d


def selections(d):
    """One row per (match, outcome) with the close, the de-vigged p and the realised return."""
    ov = 1 / d.PSCH + 1 / d.PSCD + 1 / d.PSCA
    parts = []
    for side, oc, mx, op in [('H', 'PSCH', 'MaxCH', 'PSH'), ('D', 'PSCD', 'MaxCD', 'PSD'), ('A', 'PSCA', 'MaxCA', 'PSA')]:
        parts.append(pd.DataFrame({
            'mi': d.index, 'side': side, 'odds': d[oc], 'win': (d.FTR == side).astype(int),
            'code': d.code, 'tier': d.tier, 'season': d.season, 'date': d.date,
            'team': d.AwayTeam if side == 'A' else d.HomeTeam,
            'maxodds': d.get(mx), 'open': d.get(op), 'p': (1 / d[oc]) / ov}))
    x = pd.concat(parts, ignore_index=True)
    x['ret'] = x.win * x.odds - 1
    x['res'] = x.win - x.p
    # a max quote more than 25% over Pinnacle is a stale or palpable-error price, not a bet
    x['mx'] = np.where((x.maxodds > 1) & (x.maxodds <= x.odds * 1.25), x.maxodds, np.nan)
    x['test'] = x.season >= TEST_FROM
    x['band'] = pd.cut(x.odds, [1, 1.6, 2.2, 3, 4.5, 8, 100], labels=['<1.6', '1.6-2.2', '2.2-3', '3-4.5', '4.5-8', '8+'])
    return x


def add_features(x):
    long = x[x.side != 'D'][['mi', 'code', 'season', 'date', 'team']].sort_values(['date', 'mi'])
    long['n'] = long.groupby(['code', 'season', 'team']).cumcount()
    rnd = long.groupby('mi').n.max()
    x['rnd'] = x.mi.map(rnd)
    L = x.groupby(['code', 'season']).rnd.transform('max').clip(lower=1)
    x['phase'] = pd.cut(x.rnd / L, [-.01, .12, .5, .85, 1.01], labels=['early', 'first_half', 'second_half', 'run_in'])
    mem = set(zip(x.code, x.season, x.team))
    tier_of = dict(zip(zip(x.team, x.season), x.tier))

    def status(c, s, t, tr):
        if (c, s - 1, t) in mem:
            return 'stayed'
        prev = tier_of.get((t, s - 1))
        return 'new' if prev is None else ('promoted' if prev > tr else 'relegated')
    x['status'] = [status(*r) for r in zip(x.code, x.season, x.team, x.tier)]
    x['mvb'] = pd.cut(np.log(x.open / x.odds), [-9, -.10, -.03, .03, .10, 9],
                      labels=['drift++', 'drift', 'flat', 'steam', 'steam++'])
    return x


def ci(s):
    return 1.96 * s.std() / np.sqrt(max(len(s), 1))


def calibration(x):
    b = pd.cut(x.odds, [1, 1.3, 1.6, 2, 2.5, 3.5, 5, 8, 15, 100])
    for t in (False, True):
        g = x[x.test == t].groupby(b[x.test == t], observed=True)
        r = pd.DataFrame({'n': g.size(), 'roi_pin%': 100 * g.ret.mean(), 'ci': 100 * g.ret.apply(ci),
                          'cal_pp': 100 * g.res.mean(), 'roi_max%': 100 * (g.apply(lambda s: (s.win * s.mx - 1).mean()))})
        print(f'\n== {"test" if t else "train"} (hit - de-vigged p = cal_pp; roi_max only 2019+)')
        print(r.round(2).to_string())


def scan(x):
    from scipy import stats
    x = add_features(x)
    cells = []
    for extra in [(), ('tier',), ('phase',), ('status',), ('mvb',), ('code',), ('tier', 'phase'), ('status', 'phase'), ('mvb', 'tier')]:
        for k, g in x.groupby(['side', 'band', *extra], observed=True):
            tr, te = g[~g.test], g[g.test]
            if len(tr) < 300 or len(te) < 150:
                continue
            z = tr.res.mean() / (tr.res.std() / np.sqrt(len(tr)))
            cells.append(dict(cell=' | '.join(map(str, k)), n_tr=len(tr), n_te=len(te),
                              cal_tr=100 * tr.res.mean(), cal_te=100 * te.res.mean(), cal_te_ci=100 * ci(te.res),
                              roi_tr=100 * tr.ret.mean(), roi_te=100 * te.ret.mean(), p=2 * stats.norm.sf(abs(z))))
    c = pd.DataFrame(cells).sort_values('p')
    c['q'] = (c.p * len(c) / np.arange(1, len(c) + 1))[::-1].cummin()[::-1]
    s = c[c.q < .10].copy()
    s['replicates'] = (np.sign(s.cal_te) == np.sign(s.cal_tr)) & (s.cal_te.abs() > s.cal_te_ci)
    print(f'{len(c)} cells, {len(s)} with train q<0.10, {s.replicates.sum()} replicate on test')
    print(s.round(2).to_string(index=False))


def club_picks(x, season):
    past = x[(x.season < season) & (x.odds < CLUB_MAX_ODDS) & (x.side != 'D')]
    g = past.groupby('team').agg(n=('res', 'size'), r=('res', 'mean'), league=('code', 'last'))
    g['shrunk'] = g.r * g.n / (g.n + CLUB_SHRINK)
    return g[(g.n >= CLUB_MIN_N) & (g.shrunk >= CLUB_THRESHOLD)].sort_values('shrunk', ascending=False)


def clubs(x):
    rows = []
    for s in range(2015, int(x.season.max()) + 1):
        pick = club_picks(x, s).index
        rows.append(x[(x.season == s) & (x.odds < CLUB_MAX_ODDS) & (x.side != 'D') & x.team.isin(pick)])
    r = pd.concat(rows)
    ctrl = x[(x.season >= 2015) & (x.odds < CLUB_MAX_ODDS) & (x.side != 'D')]
    print(f'strategy  n={len(r)}  ROI@Pinnacle {100*r.ret.mean():+.2f} ±{100*ci(r.ret):.2f}  '
          f'ROI@max(2019+) {100*(r.win*r.mx-1).mean():+.2f}  cal {100*r.res.mean():+.2f}pp')
    print(f'control   n={len(ctrl)}  ROI@Pinnacle {100*ctrl.ret.mean():+.2f}  ROI@max {100*(ctrl.win*ctrl.mx-1).mean():+.2f}')
    print(r.groupby('season').ret.agg(['size', 'mean']).rename(columns={'mean': 'roi'}).round(3).T.to_string())
    agg = r.groupby('team').ret.agg(['sum', 'size'])
    rng = np.random.default_rng(0)
    bs = [agg.loc[k, 'sum'].sum() / agg.loc[k, 'size'].sum() for k in (rng.choice(agg.index, len(agg)) for _ in range(4000))]
    print('team-clustered 95%% CI [%+.2f, %+.2f]' % tuple(100 * np.percentile(bs, [2.5, 97.5])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calibration', action='store_true')
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--clubs', action='store_true')
    ap.add_argument('--picks', type=int)
    ap.add_argument('--no-download', action='store_true')
    a = ap.parse_args()
    if not a.no_download:
        download()
    x = selections(load())
    print(f'{x.mi.nunique()} matches, seasons {x.season.min()}-{x.season.max()}', file=sys.stderr)
    if a.calibration:
        calibration(x)
    if a.scan:
        scan(x)
    if a.clubs:
        clubs(x)
    if a.picks:
        print(club_picks(x, a.picks).round(3).to_string())


if __name__ == '__main__':
    main()
