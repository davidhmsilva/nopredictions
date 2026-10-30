#!/usr/bin/env python3
"""
H-PM-INPLAY-CAL (research_hypotheses #44, db/068) — is Polymarket's in-play
football price calibrated?

Pre-registered BEFORE any price was compared with an outcome. `--build` is
outcome-blind (observations, match clock, costs, what the price is);
`--report` runs the test exactly as registered.

Data — all free, all loaded on 2026-10-03:
  ingest/data/price_paths/polymarket  Stage M: token0's DISPLAYED price, one point a
                                      minute, listed kick-off −90' → +180'
  venue_market_history                family, line, payout0 (0/1), fee_rate, volume
  espn_matches / espn_match_events    goal minutes with stoppage (Stage L)
  agent/data/soccer_live              the CLOB's bid/ask every minute, 2026-09-13 → 09-27

🔑 The match clock. Path minutes count from the LISTED kick-off, and a match
kicks off late and breaks for 15-20'. Each fixture is anchored on its own
goals: the minute all its markets jump together, matched to ESPN's goal
minute, gives the first- and second-half offsets. A goal enters the score at
the minute the market moved, so no price is ever compared with a state it
could not yet know.

Usage:
    python pm_inplay_calibration.py --build
    python pm_inplay_calibration.py --report
    python pm_inplay_calibration.py --coherence   # post-hoc validity check, labelled as such
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'ingest'))
load_dotenv(ROOT / 'ingest' / '.env')
from db_pool import ingest_url  # noqa: E402

PATHS = ROOT / 'ingest' / 'data' / 'price_paths' / 'polymarket'
TAPE = ROOT / 'agent' / 'data' / 'soccer_live'
CACHE = ROOT / 'agent' / 'data' / 'pm_inplay_cal'

SPLIT = pd.Timestamp('2026-03-01', tz='UTC')
FAMILIES = ['moneyline', 'totals', 'btts', 'spreads']
PHASES = ['PRE', '1H', '2H-A', '2H-B']       # PRE −30..−1 from listed KO; 1H 1-45'; 2H-A 46-69'; 2H-B 70-95'
EDGES = [0.02, 0.05, 0.10, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.90, 0.95, 0.9800001]
BUCKETS = [f'{a:.2f}-{min(b, 0.98):.2f}' for a, b in zip(EDGES[:-1], EDGES[1:])]

TMIN, TMAX = -30, 130        # observation window, wall minutes from listed KO
W1 = (0, 14)                 # first half:  wall − goal minute
W2 = (12, 40)                # second half: + HT break + first-half stoppage
JUMP_MIN = 0.10              # summed |Δp| across a fixture's markets that counts as "the market moved"
P_LO, P_HI = 0.02, 0.98
TRUST_MIN, TRUST_N = 0.95, 200   # amendment: cells whose displayed price came from a two-sided book
B = 2000
RNG = np.random.default_rng(20261004)


# ---------------------------------------------------------------------------
# Build (outcome-blind)
# ---------------------------------------------------------------------------

def load_markets(cur) -> dict:
    cur.execute("""SELECT market_id, match_id, family, line, payout0, fee_rate, volume_usd,
                          kickoff_utc, subject_side
                   FROM venue_market_history
                   WHERE venue = 'polymarket' AND prices_status = 'ok'""")
    out = {}
    for mid, match_id, fam, line, y, fee, vol, ko, side in cur.fetchall():
        out[mid] = {'match_id': match_id, 'family': fam, 'line': float(line) if line is not None else np.nan,
                    'y': int(y), 'fee': float(fee or 0), 'vol': float(vol or 0), 'ko': ko, 'side': side}
    return out


def load_timelines(cur, match_ids) -> dict:
    cur.execute("""SELECT match_id, event_id, timeline_ok, went_to_et FROM espn_matches
                   WHERE match_id = ANY(%s)""", (list(match_ids),))
    fx = {mid: {'eid': eid, 'ok': bool(ok), 'et': bool(et), 'goals': []}
          for mid, eid, ok, et in cur.fetchall()}
    by_eid = {v['eid']: mid for mid, v in fx.items()}
    cur.execute("""SELECT event_id, minute, added, period, team_side FROM espn_match_events
                   WHERE event_id = ANY(%s) AND kind IN ('goal', 'own_goal', 'penalty_goal')
                   ORDER BY event_id, seq""", (list(by_eid),))
    for eid, minute, added, period, side in cur.fetchall():
        if period in (1, 2) and minute is not None and side in ('H', 'A'):
            fx[by_eid[eid]]['goals'].append((period, minute + (added or 0), side))
    return fx


def load_paths(universe: dict) -> dict:
    paths = {}
    for f in sorted(PATHS.glob('*.jsonl.gz')):
        with gzip.open(f, 'rt') as fh:
            for line in fh:
                r = json.loads(line)
                if r['m'] in universe and r['t']:
                    paths[r['m']] = (np.asarray(r['t'], np.int16), np.asarray(r['p'], np.float32))
    return paths


def dense(t, p, lo=-90, hi=179):
    row = np.full(hi - lo + 1, np.nan, np.float32)
    ok = (t >= lo) & (t <= hi)
    row[t[ok] - lo] = p[ok]
    return row


def anchor_goals(J: np.ndarray, goals: list, lo=-90):
    """Match each regulation goal to the minute the fixture's markets jumped.
    Returns [(period, g, side, wall or None)], in order."""
    out, prev = [], -10**6
    for period, g, side in goals:
        a, b = (W1 if period == 1 else W2)
        start, end = max(g + a, prev + 1), g + b
        if start > end:
            out.append((period, g, side, None)); continue
        i0, i1 = start - lo, end - lo + 1
        seg = J[i0:i1]
        if seg.size == 0 or not np.isfinite(seg).any():
            out.append((period, g, side, None)); continue
        k = int(np.nanargmax(seg))
        if seg[k] >= JUMP_MIN:
            wall = start + k
            out.append((period, g, side, wall))
            prev = wall
        else:
            out.append((period, g, side, None))
    return out


def build() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(ingest_url())
    cur = conn.cursor()
    mk = load_markets(cur)
    tl = load_timelines(cur, {m['match_id'] for m in mk.values()})
    conn.close()
    paths = load_paths(mk)
    print(f'markets {len(mk)}, with a path {len(paths)}, fixtures {len({m["match_id"] for m in mk.values()})}, '
          f'with an ESPN timeline {sum(1 for v in tl.values() if v["ok"])}')

    by_fx = defaultdict(list)
    for mid, (t, p) in paths.items():
        by_fx[mk[mid]['match_id']].append(mid)

    # Pass 1: anchor every fixture's goals on its own markets.
    anchors, offs1, offs2, gaps = {}, [], [], []
    for fx, mids in by_fx.items():
        info = tl.get(fx)
        if not info or not info['ok'] or info['et']:
            continue
        M = np.vstack([dense(*paths[m]) for m in mids])
        D = np.abs(np.diff(M, axis=1))
        D = np.where(np.isfinite(D), np.minimum(D, 0.5), 0.0)
        J = np.concatenate([[0.0], D.sum(axis=0)])
        a = anchor_goals(J, info['goals'])
        o1 = [w - g for per, g, s, w in a if per == 1 and w is not None]
        o2 = [w - g for per, g, s, w in a if per == 2 and w is not None]
        a1 = float(np.median(o1)) if o1 else None
        a2 = float(np.median(o2)) if o2 else None
        anchors[fx] = (a, a1, a2)
        if a1 is not None:
            offs1.append(a1)
        if a2 is not None:
            offs2.append(a2)
        if a1 is not None and a2 is not None:
            gaps.append(a2 - a1)
    A1 = float(np.median(offs1)); GAP = float(np.median(gaps))
    n_goals = sum(len(a) for a, _, _ in anchors.values())
    n_res = sum(1 for a, _, _ in anchors.values() for x in a if x[3] is not None)
    print(f'clock: {len(anchors)} fixtures anchored; goals {n_goals}, matched to a market jump {n_res} '
          f'({n_res / max(n_goals, 1):.1%}); median first-half offset {A1:+.0f}\', '
          f'second-half minus first-half {GAP:+.0f}\' (p10/p90 {np.percentile(gaps, 10):.0f}/{np.percentile(gaps, 90):.0f})')

    # Pass 2: observations.
    cols = defaultdict(list)
    fam_ix = {f: i for i, f in enumerate(FAMILIES)}
    side_ix = {None: 0, 'home': 1, 'away': 2, 'draw': 3}
    clock_counts = defaultdict(int)
    tt = np.arange(TMIN, TMAX + 1, dtype=np.int16)
    for fx, mids in by_fx.items():
        ko = mk[mids[0]]['ko']
        phase = np.full(tt.size, -1, np.int8)
        phase[tt < 0] = 0
        minute = np.full(tt.size, -99, np.int16)
        hg = np.zeros(tt.size, np.int8); ag = np.zeros(tt.size, np.int8)
        gw = np.zeros(tt.size, bool); pj = np.zeros(tt.size, bool)
        src = 'none'
        if fx in anchors:
            a, a1, a2 = anchors[fx]
            if a1 is None and a2 is None:
                a1, a2, src = A1, A1 + GAP, 'default'
            elif a1 is None:
                a1, src = a2 - GAP, 'one_half'
            elif a2 is None:
                a2, src = a1 + GAP, 'one_half'
            else:
                src = 'both_halves'
            if not (8 <= a2 - a1 <= 35):
                src = 'implausible'
            else:
                end1 = max([47] + [g + 1 for per, g, s, w in a if per == 1])
                m1, m2 = tt - a1, tt - a2
                in1 = (tt >= 0) & (m1 >= 1) & (m1 <= end1)
                in2 = (m2 >= 46) & (m2 <= 95)
                phase[in1] = 1
                phase[in2 & (m2 <= 69)] = 2
                phase[in2 & (m2 >= 70)] = 3
                minute[in1] = np.round(m1[in1]).astype(np.int16)
                minute[in2] = np.round(m2[in2]).astype(np.int16)
                for per, g, s, w in a:
                    wall = w if w is not None else int(round(g + (a1 if per == 1 else a2)))
                    after = tt >= wall
                    if s == 'H':
                        hg += after
                    else:
                        ag += after
                    gw |= (tt >= wall) & (tt <= wall + 2)
                    pj |= tt == wall - 1
        if src not in ('both_halves', 'one_half', 'default'):
            phase[phase > 0] = -1            # no clock, no in-play phase: PRE only
        clock_counts[src] += 1
        kod = pd.Timestamp(ko)
        for mid in mids:
            m = mk[mid]
            row = dense(*paths[mid], lo=TMIN, hi=TMAX)
            keep = np.isfinite(row) & (phase >= 0)
            if not keep.any():
                continue
            n = int(keep.sum())
            fam = m['family']
            tot = (hg + ag)[keep]
            if fam == 'totals':
                decided = tot > m['line']
            elif fam == 'btts':
                decided = (hg[keep] >= 1) & (ag[keep] >= 1)
            else:
                decided = np.zeros(n, bool)
            cols['fx'].append(np.full(n, fx, np.int32))
            cols['mkt'].append(np.full(n, hash(mid) & 0x7FFFFFFF, np.int32))
            cols['fam'].append(np.full(n, fam_ix[fam], np.int8))
            cols['side'].append(np.full(n, side_ix.get(m['side'], 0), np.int8))
            cols['line'].append(np.full(n, m['line'], np.float32))
            cols['t'].append(tt[keep])
            cols['phase'].append(phase[keep])
            cols['minute'].append(minute[keep])
            cols['hg'].append(hg[keep]); cols['ag'].append(ag[keep])
            cols['p'].append(row[keep])
            cols['y'].append(np.full(n, m['y'], np.int8))
            cols['fee'].append(np.full(n, m['fee'], np.float32))
            cols['vol'].append(np.full(n, m['vol'], np.float32))
            cols['decided'].append(decided)
            cols['gw'].append(gw[keep]); cols['pj'].append(pj[keep])
            cols['test'].append(np.full(n, kod >= SPLIT, bool))
    df = pd.DataFrame({k: np.concatenate(v) for k, v in cols.items()})
    df.to_pickle(CACHE / 'observations.pkl')
    print(f'clock sources: {dict(clock_counts)}')
    print(f'observations: {len(df):,} rows, {df.fx.nunique():,} fixtures; by phase '
          + ', '.join(f'{PHASES[i]} {int((df.phase == i).sum()):,}' for i in range(4)))
    costs()


# ---------------------------------------------------------------------------
# What the price is, and what crossing the spread costs (live tape, outcome-blind)
# ---------------------------------------------------------------------------

TAPE_FAMILY = {'moneyline': 'moneyline', 'totals': 'totals', 'btts': 'btts',
               'both_teams_to_score': 'btts', 'spreads': 'spreads'}


def tape_phase(g: dict | None, ts: datetime, ko: datetime) -> int:
    if g and g.get('live'):
        per, el = g.get('period'), str(g.get('elapsed') or '')
        try:
            e = int(el.split('+')[0]) if el else None
        except ValueError:
            e = None
        if per == '1H' and e is not None and e >= 1:
            return 1
        if per == '2H' and e is not None:
            return 2 if e <= 69 else 3
        return -1
    mins = (ko - ts).total_seconds() / 60
    return 0 if 1 <= mins <= 30 else -1


def costs() -> None:
    conn = psycopg2.connect(ingest_url())
    cur = conn.cursor()
    cur.execute("""SELECT market_id FROM venue_market_history
                   WHERE venue = 'polymarket' AND prices_status = 'ok' AND kickoff_utc >= '2026-09-12'""")
    wanted = {r[0] for r in cur.fetchall()}
    conn.close()
    path_of = {}
    for f in sorted(PATHS.glob('2026-09*.jsonl.gz')):
        with gzip.open(f, 'rt') as fh:
            for line in fh:
                r = json.loads(line)
                if r['m'] in wanted and r['t']:
                    path_of[r['m']] = (r['ko'], dict(zip(r['t'], r['p'])))

    spreads = defaultdict(list)      # (family, phase, bucket) -> half-spreads, two-sided books
    books = defaultdict(lambda: [0, 0])   # (family, phase) -> [two-sided, all]
    cmp = []                          # (p, bid, ask, last, family, phase)
    thin = []                         # the same, on books that are NOT two-sided
    seen_fams = defaultdict(int)
    for f in sorted(TAPE.glob('2026-09-*.meta.jsonl.gz')):
        day = f.name[:10]
        meta = {}
        with gzip.open(f, 'rt') as fh:
            for line in fh:
                m = json.loads(line)
                meta[int(m['i'])] = m
        snap_file = TAPE / f'{day}.jsonl.gz'
        if not snap_file.exists():
            continue
        with gzip.open(snap_file, 'rt') as fh:
            for line in fh:
                if not line.strip():
                    continue
                s = json.loads(line)
                ts = datetime.fromisoformat(s['ts'])
                games = {g.get('g'): g for g in s.get('games', [])}
                for row in s['q']:
                    m = meta.get(row[0])
                    if not m:
                        continue
                    seen_fams[m.get('family')] += 1
                    fam = TAPE_FAMILY.get(m.get('family'))
                    if fam is None:
                        continue
                    bid, ask, last = row[1], row[3], row[7]
                    ko = datetime.fromisoformat(m['kickoff'].replace('Z', '+00:00'))
                    ph = tape_phase(games.get(m.get('group')), ts, ko)
                    if ph < 0:
                        continue
                    two = bid is not None and ask is not None and 0 < bid < ask < 1
                    books[(fam, ph)][1] += 1
                    pth = path_of.get(m['cid'])
                    if not two:
                        if pth:
                            t = int(round((ts.timestamp() - pth[0]) / 60))
                            p = pth[1].get(t)
                            if p is not None:
                                thin.append((p, bid, ask, last, fam, ph))
                        continue
                    books[(fam, ph)][0] += 1
                    mid = (bid + ask) / 2
                    if not P_LO <= mid <= P_HI:
                        continue
                    b = int(np.searchsorted(EDGES, mid, side='right') - 1)
                    spreads[(fam, ph, b)].append((ask - bid) / 2)
                    if pth:
                        t = int(round((ts.timestamp() - pth[0]) / 60))
                        p = pth[1].get(t)
                        if p is not None:
                            cmp.append((p, bid, ask, last, fam, ph))

    rows = []
    for (fam, ph, b), xs in spreads.items():
        xs = np.asarray(xs)
        rows.append({'family': fam, 'phase': PHASES[ph], 'bucket': BUCKETS[b], 'n': len(xs),
                     'half_spread_med': float(np.median(xs)), 'half_spread_p75': float(np.percentile(xs, 75))})
    ct = pd.DataFrame(rows).sort_values(['family', 'phase', 'bucket'])
    ct.to_csv(CACHE / 'costs.csv', index=False)
    bk = pd.DataFrame([{'family': f, 'phase': PHASES[ph], 'two_sided': a, 'quotes': n, 'share': a / n}
                       for (f, ph), (a, n) in books.items()]).sort_values(['family', 'phase'])
    bk.to_csv(CACHE / 'books.csv', index=False)
    print('tape families seen:', dict(sorted(seen_fams.items(), key=lambda kv: -kv[1])[:12]))
    print('\ntwo-sided share of quotes by family x phase:')
    print(bk.to_string(index=False))
    print('\nmedian half-spread (pp) by family x phase, all prices:')
    allp = defaultdict(list)
    for (fam, ph, b), xs in spreads.items():
        allp[(fam, PHASES[ph])].extend(xs)
    for k in sorted(allp):
        print(f'  {k[0]:9s} {k[1]:5s} n={len(allp[k]):7d}  median {100 * np.median(allp[k]):.2f}  '
              f'p75 {100 * np.percentile(allp[k], 75):.2f}')
    if cmp:
        c = pd.DataFrame(cmp, columns=['p', 'bid', 'ask', 'lastp', 'family', 'phase'])
        c['mid'] = (c.bid + c.ask) / 2
        inside = ((c.p >= c.bid - 1e-9) & (c.p <= c.ask + 1e-9)).mean()
        print(f'\nwhat the history price IS — {len(c):,} tape minutes matched to a path:')
        print(f'  |p − mid| median {100 * (c.p - c.mid).abs().median():.2f}pp, '
              f'mean (p − mid) {100 * (c.p - c.mid).mean():+.3f}pp, inside [bid, ask] {inside:.1%}, '
              f'|p − last trade| < 0.5pp {((c.p - c.lastp).abs() < 0.005).mean():.1%}')
        c.to_pickle(CACHE / 'price_vs_book.pkl')
    if thin:
        h = pd.DataFrame(thin, columns=['p', 'bid', 'ask', 'lastp', 'family', 'phase'])
        side = np.where(h.bid.fillna(0) > 0, 'bid only', np.where(h.ask.fillna(1) < 1, 'ask only', 'empty'))
        h['book'] = side
        print(f'\non books that are NOT two-sided — {len(h):,} tape minutes matched to a path:')
        for k, g in h.groupby('book'):
            near_last = ((g.p - g.lastp).abs() < 0.005).mean()
            print(f'  {k:9s} {len(g):7,d}  p == last trade {near_last:.1%}, '
                  f'p in [0.02, 0.98] {((g.p >= P_LO) & (g.p <= P_HI)).mean():.1%}')
        h.to_pickle(CACHE / 'price_vs_thin_book.pkl')
        trust_table(c, h)


def trust_table(c: pd.DataFrame, h: pd.DataFrame) -> pd.DataFrame:
    """P(the book was two-sided | family, phase, DISPLAYED-price bucket) — the only
    thing history can be filtered on, since a path carries the price and not the book."""
    x = pd.concat([c[['p', 'family', 'phase']].assign(two=True), h[['p', 'family', 'phase']].assign(two=False)])
    x = x[(x.p >= P_LO) & (x.p <= P_HI) & (x.p.round(4) != 0.5)]
    x['bucket'] = pd.Categorical.from_codes(np.searchsorted(EDGES, x.p, side='right') - 1, BUCKETS)
    x['phase'] = x.phase.map(dict(enumerate(PHASES)))
    t = x.groupby(['family', 'phase', 'bucket'], observed=True).two.agg(['mean', 'size']).reset_index()
    t.columns = ['family', 'phase', 'bucket', 'two_sided_share', 'tape_minutes']
    t.to_csv(CACHE / 'two_sided_given_p.csv', index=False)
    return t


# ---------------------------------------------------------------------------
# Report — the pre-registered test (touches outcomes)
# ---------------------------------------------------------------------------

def cell_stats(g: pd.DataFrame) -> dict:
    """g: one row per (fixture) with columns r (fixture mean y−p), fee (fixture mean fee cost)."""
    x = g['r'].to_numpy()
    n = x.size
    if n == 0:
        return {'n': 0}
    mean = float(x.mean())
    se = float(x.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
    if n >= 20:
        idx = RNG.integers(0, n, size=(B, n))
        boots = x[idx].mean(axis=1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
    else:
        lo = hi = np.nan
    from math import erf, sqrt
    z = mean / se if se and se > 0 else 0.0
    pval = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    return {'n': n, 'mean': mean, 'lo': float(lo), 'hi': float(hi), 'p': pval,
            'p_avg': float(g['pm'].mean()), 'y_avg': float(g['ym'].mean()), 'fee': float(g['fee'].mean())}


def per_fixture(d: pd.DataFrame, keys: list) -> pd.DataFrame:
    d = d.assign(r=d.y - d.p, feec=d.fee * d.p * (1 - d.p))
    return d.groupby(keys + ['fx'], observed=True).agg(
        r=('r', 'mean'), pm=('p', 'mean'), ym=('y', 'mean'), fee=('feec', 'mean')).reset_index()


def table(d: pd.DataFrame, keys: list) -> pd.DataFrame:
    pf = per_fixture(d, keys)
    rows = []
    for k, g in pf.groupby(keys, observed=True):
        k = k if isinstance(k, tuple) else (k,)
        rows.append(dict(zip(keys, k)) | cell_stats(g))
    return pd.DataFrame(rows, columns=keys + ['n', 'mean', 'lo', 'hi', 'p', 'p_avg', 'y_avg', 'fee'])


def bh(p: np.ndarray, q: float = 0.10) -> np.ndarray:
    n = p.size
    order = np.argsort(p)
    thresh = q * (np.arange(1, n + 1) / n)
    passed = p[order] <= thresh
    k = np.max(np.where(passed)[0]) + 1 if passed.any() else 0
    out = np.zeros(n, bool)
    out[order[:k]] = True
    return out


def pp(x):
    return f'{100 * x:+.2f}'


def report() -> None:
    df = pd.read_pickle(CACHE / 'observations.pkl')
    ct = pd.read_csv(CACHE / 'costs.csv')
    trust = pd.read_csv(CACHE / 'two_sided_given_p.csv')
    trusted = {(r.family, r.phase, r.bucket) for r in trust.itertuples()
               if r.two_sided_share >= TRUST_MIN and r.tape_minutes >= TRUST_N}
    # Amendment (db/068): an empty book reads exactly 0.5000 — never a price.
    base = df[(df.p >= P_LO) & (df.p <= P_HI) & (df.p.round(4) != 0.5)]
    decided = base[base.decided & (base.phase > 0)]
    d = base[~base.decided & ~base.gw].copy()
    d['family'] = pd.Categorical.from_codes(d.fam, FAMILIES)
    d['phase_n'] = pd.Categorical.from_codes(d.phase, PHASES)
    d['bucket'] = pd.Categorical.from_codes(np.searchsorted(EDGES, d.p, side='right') - 1, BUCKETS)
    d['split'] = np.where(d.test, 'TEST', 'DISC')
    key = list(zip(d.family.astype(str), d.phase_n.astype(str), d.bucket.astype(str)))
    d['trusted'] = [k in trusted for k in key]
    n_all = len(d)
    d = d[d.trusted]
    out = []
    w = out.append
    w(f'# H-PM-INPLAY-CAL — results ({datetime.now():%Y-%m-%d})\n')
    w(f'Observations after exclusions: {n_all:,}; in TRUSTED cells (>= {TRUST_MIN:.0%} two-sided on the tape): '
      f'{len(d):,} over {d.fx.nunique():,} fixtures. Decided-by-score rows set aside: {len(decided):,}.\n')
    w('Trusted cells: ' + '; '.join(f'{f} {ph}: ' + ', '.join(sorted(b for (ff, pp_, b) in trusted if ff == f and pp_ == ph))
                                     for f in FAMILIES for ph in PHASES
                                     if any(ff == f and pp_ == ph for (ff, pp_, b) in trusted)) + '\n')

    # P1: family × phase, all prices pooled.
    t1 = table(d, ['split', 'family', 'phase_n'])
    w('\n## P1 — calibrated within ±1.0pp per family × phase (all prices)\n')
    w('| family | phase | DISC n | DISC y−p (pp) | TEST n | TEST y−p (pp) | TEST 95% CI | within ±1pp |')
    w('|---|---|--:|--:|--:|--:|---|---|')
    p1_fail = []
    for fam in FAMILIES:
        for ph in PHASES:
            a = t1[(t1.split == 'DISC') & (t1.family == fam) & (t1.phase_n == ph)]
            b = t1[(t1.split == 'TEST') & (t1.family == fam) & (t1.phase_n == ph)]
            if b.empty:
                continue
            a = a.iloc[0] if not a.empty else None
            b = b.iloc[0]
            ok = (b.lo >= -0.01) and (b.hi <= 0.01)
            if ph != 'PRE' and not ok:
                p1_fail.append((fam, ph, b["mean"], b.lo, b.hi))
            w(f'| {fam} | {ph} | {int(a.n) if a is not None else 0} | {pp(a["mean"]) if a is not None else "—"} | '
              f'{int(b.n)} | {pp(b["mean"])} | [{pp(b.lo)}, {pp(b.hi)}] | {"yes" if ok else "**no**"} |')

    # Reliability by phase, families pooled.
    t2 = table(d, ['split', 'phase_n', 'bucket'])
    w('\n## Reliability — families pooled, TEST (DISC beside it)\n')
    for ph in PHASES:
        w(f'\n**{ph}**\n')
        w('| price | TEST n | avg price | realised | y−p (pp) | 95% CI | DISC y−p |')
        w('|---|--:|--:|--:|--:|---|--:|')
        for bk in BUCKETS:
            b = t2[(t2.split == 'TEST') & (t2.phase_n == ph) & (t2.bucket == bk)]
            a = t2[(t2.split == 'DISC') & (t2.phase_n == ph) & (t2.bucket == bk)]
            if b.empty:
                continue
            b = b.iloc[0]
            w(f'| {bk} | {int(b.n)} | {b.p_avg:.3f} | {b.y_avg:.3f} | {pp(b["mean"])} | [{pp(b.lo)}, {pp(b.hi)}] | '
              f'{pp(a.iloc[0]["mean"]) if not a.empty else "—"} |')

    # P2: late longshots and favourites, families pooled, 2H-A ∪ 2H-B.
    late = d[d.phase >= 2].copy()
    late['tailc'] = np.where(late.p < 0.15, 'longshot <0.15', np.where(late.p > 0.85, 'favourite >0.85', 'middle'))
    t3 = table(late[late.tailc != 'middle'], ['split', 'tailc'])
    w('\n## P2 — late longshots below price, late favourites above (2H, families pooled)\n')
    w('| tail | DISC n | DISC y−p | TEST n | TEST y−p | TEST 95% CI | predicted sign held |')
    w('|---|--:|--:|--:|--:|---|---|')
    for tail, sign in (('longshot <0.15', -1), ('favourite >0.85', +1)):
        a = t3[(t3.split == 'DISC') & (t3.tailc == tail)]
        b = t3[(t3.split == 'TEST') & (t3.tailc == tail)]
        if b.empty:
            continue
        b = b.iloc[0]
        held = (b.hi < 0) if sign < 0 else (b.lo > 0)
        w(f'| {tail} | {int(a.iloc[0].n) if not a.empty else 0} | {pp(a.iloc[0]["mean"]) if not a.empty else "—"} | '
          f'{int(b.n)} | {pp(b["mean"])} | [{pp(b.lo)}, {pp(b.hi)}] | {"yes" if held else "no"} |')

    # P3: late overs.
    ov = d[(d.family == 'totals') & (d.phase == 3) & (d.p >= 0.15) & (d.p <= 0.85)]
    t4 = table(ov, ['split'])
    w('\n## P3 — totals Over in 2H-B priced 0.15-0.85 resolve above price\n')
    if t4.empty:
        w('**Not measurable from history mids**: no totals cell in 2H-B reaches the trust bar — '
          'its books are two-sided on 36-76% of tape minutes. Measured at real quotes below.\n')
    w('| split | n fixtures | avg price | realised | y−p (pp) | 95% CI |')
    w('|---|--:|--:|--:|--:|---|')
    for s in ('DISC', 'TEST'):
        b = t4[t4.split == s]
        if not b.empty:
            b = b.iloc[0]
            w(f'| {s} | {int(b.n)} | {b.p_avg:.3f} | {b.y_avg:.3f} | {pp(b["mean"])} | [{pp(b.lo)}, {pp(b.hi)}] |')

    # The scan: every family × in-play phase × bucket, against the decision rule.
    ip = d[d.phase > 0]
    t5 = table(ip, ['split', 'family', 'phase_n', 'bucket'])
    test = t5[t5.split == 'TEST'].copy()
    disc = t5[t5.split == 'DISC'].set_index(['family', 'phase_n', 'bucket'])
    cost_ix = ct.set_index(['family', 'phase', 'bucket'])['half_spread_med']
    fam_ph_cost = ct.groupby(['family', 'phase'])['half_spread_med'].median()

    def cost_of(r):
        hs = cost_ix.get((r.family, r.phase_n, r.bucket))
        if hs is None or (isinstance(hs, float) and np.isnan(hs)):
            hs = fam_ph_cost.get((r.family, r.phase_n), 0.03)
        return float(hs) + r.fee

    test['cost'] = test.apply(cost_of, axis=1)
    test = test[test.n >= 1]
    test['bh'] = bh(test.p.fillna(1).to_numpy())
    test['disc_mean'] = [disc.loc[(r.family, r.phase_n, r.bucket), 'mean']
                         if (r.family, r.phase_n, r.bucket) in disc.index else np.nan for r in test.itertuples()]
    test['passes'] = ((test.n >= 200) & ((test.lo > 0) | (test.hi < 0)) & (test["mean"].abs() > test.cost)
                    & (np.sign(test["mean"]) == np.sign(test.disc_mean)) & test.bh)
    test.to_csv(CACHE / 'scan_test.csv', index=False)
    w(f'\n## The scan — {len(test)} cells (family × in-play phase × price bucket), decision rule\n')
    w(f'CI excluding 0: {int(((test.lo > 0) | (test.hi < 0)).sum())} · survive BH-FDR q<=0.10: '
      f'{int(test.bh.sum())} · n>=200: {int((test.n >= 200).sum())} · **pass the full rule: {int(test["passes"].sum())}**\n')
    show = test[(test.lo > 0) | (test.hi < 0)].sort_values('p').head(25)
    if len(show):
        w('| family | phase | price | TEST n | y−p (pp) | 95% CI | cost (pp) | DISC y−p | BH | pass |')
        w('|---|---|---|--:|--:|---|--:|--:|---|---|')
        for r in show.itertuples():
            w(f'| {r.family} | {r.phase_n} | {r.bucket} | {int(r.n)} | {pp(r.mean)} | [{pp(r.lo)}, {pp(r.hi)}] | '
              f'{100 * r.cost:.2f} | {pp(r.disc_mean) if np.isfinite(r.disc_mean) else "—"} | '
              f'{"yes" if r.bh else "no"} | {"**YES**" if r.passes else "no"} |')

    # Sensitivity.
    w('\n## Sensitivity (TEST, in-play, families pooled)\n')
    w('| variant | n fixtures | y−p (pp) | 95% CI |')
    w('|---|--:|--:|---|')
    variants = {
        'as registered': ip,
        'also drop the minute before each goal reaction': ip[~ip.pj],
        'volume >= $5k': ip[ip.vol >= 5000],
        'fee-free markets': ip[ip.fee == 0],
        'fee-charging markets': ip[ip.fee > 0],
    }
    for name, v in variants.items():
        s = table(v[v.split == 'TEST'], ['split'])
        if not s.empty:
            s = s.iloc[0]
            w(f'| {name} | {int(s.n)} | {pp(s["mean"])} | [{pp(s.lo)}, {pp(s.hi)}] |')

    # Decided markets still priced inside [0.02, 0.98].
    if len(decided):
        dd = decided.assign(r=decided.y - decided.p)
        w('\n## Set aside: markets the score had already decided, still quoted 0.02-0.98\n')
        w(f'{len(dd):,} minute-rows over {dd.fx.nunique():,} fixtures; average price {dd.p.mean():.3f}, '
          f'paid {dd.y.mean():.3f}. (This is H-SETTLED-SWEEP''s in-match window; its known risk is a goal '
          f'that does not stand.)\n')

    w('\n## Verdict\n')
    passed = int(test['passes'].sum())
    if passed == 0 and not p1_fail:
        w('P1 holds: no family × phase leaves ±1pp and no cell clears the decision rule. '
          'Polymarket\'s in-play price is calibrated within its costs.')
    else:
        w(f'P1 cells outside ±1pp: {len(p1_fail)}; cells clearing the full decision rule: {passed}. '
          'See the tables above; nothing below the rule is an edge.')
    out.extend(tape_test())
    text = '\n'.join(out)
    (CACHE / 'report.md').write_text(text)
    print(text)


def tape_test() -> list:
    """Amendment (3), secondary: calibration at REAL quotes on the live tape,
    2026-09-13 → 09-27 (all inside TEST). The only Polymarket data where the book
    is known — so the only place the tails (P2) and late totals (P3) can be
    measured, and at the ask, which is what a taker pays."""
    conn = psycopg2.connect(ingest_url())
    cur = conn.cursor()
    cur.execute("""SELECT market_id, match_id, payout0, fee_rate, token0 FROM venue_market_history
                   WHERE venue = 'polymarket' AND kickoff_utc >= '2026-09-12'
                     AND payout0 IS NOT NULL AND match_id IS NOT NULL""")
    outcome = {m: (fx, int(y), float(fee or 0), tok) for m, fx, y, fee, tok in cur.fetchall()}
    conn.close()
    fam_ix = {f: i for i, f in enumerate(FAMILIES)}
    rows, skipped = [], defaultdict(int)
    for mf in sorted(TAPE.glob('2026-09-*.meta.jsonl.gz')):
        day = mf.name[:10]
        meta = {}
        with gzip.open(mf, 'rt') as fh:
            for line in fh:
                m = json.loads(line)
                meta[int(m['i'])] = m
        sf = TAPE / f'{day}.jsonl.gz'
        if not sf.exists():
            continue
        last_score, changed_at = {}, {}
        with gzip.open(sf, 'rt') as fh:
            for line in fh:
                if not line.strip():
                    continue
                snap = json.loads(line)
                ts = datetime.fromisoformat(snap['ts'])
                games = {}
                for g in snap.get('games', []):
                    games[g.get('g')] = g
                    sc = g.get('score')
                    if sc and last_score.get(g.get('g')) != sc:
                        if g.get('g') in last_score:
                            changed_at[g.get('g')] = ts
                        last_score[g.get('g')] = sc
                for r in snap['q']:
                    m = meta.get(r[0])
                    if not m or m['cid'] not in outcome:
                        continue
                    fam = TAPE_FAMILY.get(m.get('family'))
                    if fam is None:
                        continue
                    fx, y, fee, tok = outcome[m['cid']]
                    if tok and m.get('token0') and str(tok) != str(m['token0']):
                        skipped['token order differs'] += 1
                        continue
                    bid, ask = r[1], r[3]
                    if not (bid is not None and ask is not None and 0 < bid < ask < 1):
                        continue
                    ko = datetime.fromisoformat(m['kickoff'].replace('Z', '+00:00'))
                    g = games.get(m.get('group'))
                    ph = tape_phase(g, ts, ko)
                    if ph < 0:
                        continue
                    if ph > 0:
                        ch = changed_at.get(m.get('group'))
                        if ch is not None and (ts - ch).total_seconds() < 180:
                            skipped['within 3 min of a score change'] += 1
                            continue
                        try:
                            hs, as_ = (int(x) for x in str(g.get('score')).split('-')[:2])
                        except (ValueError, AttributeError):
                            skipped['no score'] += 1
                            continue
                        line_v = float(m['line']) if m.get('line') not in (None, 'None', '') else None
                        if fam == 'totals' and line_v is not None and hs + as_ > line_v:
                            skipped['decided'] += 1
                            continue
                        if fam == 'btts' and hs >= 1 and as_ >= 1:
                            skipped['decided'] += 1
                            continue
                    mid = (bid + ask) / 2
                    if not P_LO <= mid <= P_HI:
                        continue
                    rows.append((fx, fam_ix[fam], ph, mid, bid, ask, y, fee))
    t = pd.DataFrame(rows, columns=['fx', 'fam', 'phase', 'p', 'bid', 'ask', 'y', 'fee'])
    t['family'] = pd.Categorical.from_codes(t.fam, FAMILIES)
    t['phase_n'] = pd.Categorical.from_codes(t.phase, PHASES)
    t['buy'] = t.y - t.ask - t.fee * t.ask * (1 - t.ask)          # take token0 at the ask
    t['sell'] = t.bid - t.y - t.fee * t.bid * (1 - t.bid)         # take token1 at 1 − bid

    def stats(v: pd.DataFrame, col: str) -> dict:
        g = v.groupby('fx')[col].mean().to_numpy()
        n = g.size
        if n < 20:
            return {'n': n, 'mean': np.nan, 'lo': np.nan, 'hi': np.nan}
        idx = RNG.integers(0, n, size=(B, n))
        bs = g[idx].mean(axis=1)
        lo, hi = np.percentile(bs, [2.5, 97.5])
        return {'n': n, 'mean': float(g.mean()), 'lo': float(lo), 'hi': float(hi)}

    t['r'] = t.y - t.p
    o = ['\n## Secondary — at REAL quotes on the live tape (2026-09-13 → 09-27, all TEST)\n',
         f'{len(t):,} minute-quotes on two-sided books over {t.fx.nunique()} fixtures. Skipped: '
         + ', '.join(f'{k} {v:,}' for k, v in skipped.items()) + '.\n',
         '| family | phase | n fx | y − mid (pp) | 95% CI | take at ask (pp) | 95% CI | take other side (pp) | 95% CI |',
         '|---|---|--:|--:|---|--:|---|--:|---|']
    for fam in FAMILIES:
        for ph in PHASES:
            v = t[(t.family == fam) & (t.phase_n == ph)]
            if v.fx.nunique() < 20:
                continue
            a, b, c = stats(v, 'r'), stats(v, 'buy'), stats(v, 'sell')
            o.append(f'| {fam} | {ph} | {a["n"]} | {pp(a["mean"])} | [{pp(a["lo"])}, {pp(a["hi"])}] | '
                     f'{pp(b["mean"])} | [{pp(b["lo"])}, {pp(b["hi"])}] | {pp(c["mean"])} | [{pp(c["lo"])}, {pp(c["hi"])}] |')
    late = t[t.phase >= 2]
    o += ['\n**P2 at real quotes** (2H, families pooled)\n',
          '| tail | n fx | y − mid (pp) | 95% CI | the predicted trade, taker (pp) | 95% CI |', '|---|--:|--:|---|--:|---|']
    for name, v, col in (('longshot mid < 0.15 — fade it (take the other side)', late[late.p < 0.15], 'sell'),
                         ('favourite mid > 0.85 — back it (take at the ask)', late[late.p > 0.85], 'buy')):
        a, b = stats(v, 'r'), stats(v, col)
        o.append(f'| {name} | {a["n"]} | {pp(a["mean"])} | [{pp(a["lo"])}, {pp(a["hi"])}] | {pp(b["mean"])} | '
                 f'[{pp(b["lo"])}, {pp(b["hi"])}] |')
    v = t[(t.family == 'totals') & (t.phase == 3) & (t.p >= 0.15) & (t.p <= 0.85)]
    a, b = stats(v, 'r'), stats(v, 'buy')
    o += ['\n**P3 at real quotes** (totals Over, 70\'+, mid 0.15-0.85)\n',
          '| n fx | y − mid (pp) | 95% CI | buy the Over at the ask (pp) | 95% CI |', '|--:|--:|---|--:|---|',
          f'| {a["n"]} | {pp(a["mean"])} | [{pp(a["lo"])}, {pp(a["hi"])}] | {pp(b["mean"])} | [{pp(b["lo"])}, {pp(b["hi"])}] |']
    t.to_pickle(CACHE / 'tape_obs.pkl')
    return o


def coherence() -> None:
    """POST-HOC validity check (not part of the registered test, 2026-10-04).

    A fixture's three moneyline books — home, draw, away — sum to ~1 when all
    three are real. A one-sided book breaks the sum, because the history price
    reads ask/2 or (bid+1)/2 there. Splits every moneyline in-play row into
    coherent minutes (sum 0.98-1.04) and the rest, and runs the scan on each.
    On 2026-10-04: 87% coherent; 0 of 39 coherent cells survive BH-FDR, while the
    incoherent 13% carry -8 to -14pp — the artefact."""
    df = pd.read_pickle(CACHE / 'observations.pkl')
    ct = pd.read_csv(CACHE / 'costs.csv')
    ml = df[(df.fam == 0) & (df.phase > 0)].copy()
    piv = ml.groupby(['fx', 't', 'side']).p.first().unstack('side')
    piv = piv[[c for c in (1, 2, 3) if c in piv.columns]].dropna()
    tot = piv.sum(axis=1)
    coh = tot[(tot >= 0.98) & (tot <= 1.04)].index
    print(f'moneyline fixture-minutes with all three books: {len(piv):,}; coherent: {len(coh):,} '
          f'({len(coh) / max(len(piv), 1):.1%})')
    ml['coherent'] = pd.MultiIndex.from_frame(ml[['fx', 't']]).isin(coh)
    d = ml[(ml.p >= P_LO) & (ml.p <= P_HI) & (ml.p.round(4) != 0.5) & ~ml.gw].copy()
    d['bucket'] = pd.Categorical.from_codes(np.searchsorted(EDGES, d.p, side='right') - 1, BUCKETS)
    d['phase_n'] = pd.Categorical.from_codes(d.phase, PHASES)
    d['split'] = np.where(d.test, 'TEST', 'DISC')
    cost = ct[ct.family == 'moneyline'].set_index(['phase', 'bucket']).half_spread_med
    for label, v in (('coherent', d[d.coherent]), ('incoherent', d[~d.coherent])):
        t = table(v, ['split', 'phase_n', 'bucket'])
        te = t[t.split == 'TEST'].copy()
        di = t[t.split == 'DISC'].set_index(['phase_n', 'bucket'])['mean']
        te['cost'] = [cost.get((r.phase_n, r.bucket), 0.01) + r.fee for r in te.itertuples()]
        te['disc'] = [di.get((r.phase_n, r.bucket), np.nan) for r in te.itertuples()]
        te['bh'] = bh(te.p.fillna(1).to_numpy())
        te['passes'] = ((te.n >= 200) & ((te.lo > 0) | (te.hi < 0)) & (te['mean'].abs() > te.cost)
                        & (np.sign(te['mean']) == np.sign(te.disc)) & te.bh)
        print(f'\n{label}: {len(v):,} rows, {len(te)} cells; CI excludes 0: {int(((te.lo > 0) | (te.hi < 0)).sum())}; '
              f'BH q<=0.10: {int(te.bh.sum())}; pass the rule: {int(te.passes.sum())}')
        print(te[['phase_n', 'bucket', 'n', 'mean', 'lo', 'hi', 'disc', 'bh', 'passes']]
              .assign(mean=lambda x: (100 * x['mean']).round(2), lo=lambda x: (100 * x.lo).round(2),
                      hi=lambda x: (100 * x.hi).round(2), disc=lambda x: (100 * x.disc).round(2))
              .to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--costs', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--coherence', action='store_true', help='post-hoc validity check (not registered)')
    a = ap.parse_args()
    if a.build:
        build()
    elif a.costs:
        costs()
    if a.report:
        report()
    if a.coherence:
        coherence()


if __name__ == '__main__':
    main()
