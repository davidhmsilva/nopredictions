"""
Cross-correlate the two series recorded by live_lag_recorder.py.

The question is one-directional and specific: does Polymarket's in-play mid LAG
a faster live line, and by how many seconds? Everything here is built so a null
answer stays legible as a null rather than as a measurement artefact:

  * the resolution ceiling is reported first — you cannot detect a lag shorter
    than the sharp feed's own update cadence, so that cadence is measured, not
    assumed;
  * the outcome mapping (which PM market is the home team) is DERIVED from price
    agreement and fails closed on ambiguity — a side error inverts the sign of
    every lag, it does not blunt it;
  * cross-correlation is reported alongside an event study, because a xcorr peak
    on two random walks polled at different rates is not evidence of anything.

Usage:
    python live_lag_analyze.py reports/lag/<slug>.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

OUTCOMES = ('Home', 'Draw', 'Away')


def devig(odds: dict[str, float]) -> dict[str, float] | None:
    if not all(odds.get(o, 0) > 1.0 for o in OUTCOMES):
        return None
    raw = {o: 1.0 / odds[o] for o in OUTCOMES}
    s = sum(raw.values())
    return {o: v / s for o, v in raw.items()} if s > 0 else None


def load(path: str):
    meta, af, pm, notes = None, [], defaultdict(list), []
    for line in open(path):
        r = json.loads(line)
        k = r['kind']
        if k == 'meta':
            meta = meta or r
        elif k == 'note':
            notes.append(r)
        elif k == 'af':
            ftr = next((m for m in r['odds'] if m['name'] == 'Fulltime Result'), None)
            if not ftr:
                continue
            o, susp = {}, False
            for v in ftr['values']:
                try:
                    o[v['value']] = float(v['odd'])
                except (TypeError, ValueError):
                    continue
                susp = susp or bool(v.get('suspended'))
            p = devig(o)
            if p:
                af.append({'t': r['t'], 'update': r.get('update'), 'p': p,
                           'susp': susp, 'goals': r.get('goals'),
                           'el': (r.get('status') or {}).get('elapsed'),
                           'sec': (r.get('status') or {}).get('seconds')})
        elif k == 'pm_ws':
            ev = r.get('ev') or {}
            for pc in ev.get('price_changes', []) or []:
                try:
                    bb, ba = float(pc['best_bid']), float(pc['best_ask'])
                except (KeyError, TypeError, ValueError):
                    continue
                pm[pc['asset_id']].append({'t': r['t'], 'bid': bb, 'ask': ba,
                                           'mid': (bb + ba) / 2})
            if 'bids' in ev and ev.get('asset_id'):
                bids = [float(x['price']) for x in ev.get('bids') or []]
                asks = [float(x['price']) for x in ev.get('asks') or []]
                if bids and asks:
                    bb, ba = max(bids), min(asks)
                    pm[ev['asset_id']].append({'t': r['t'], 'bid': bb, 'ask': ba,
                                               'mid': (bb + ba) / 2})
        elif k == 'pm_rest' and r.get('bid') and r.get('ask'):
            pm[r['token']].append({'t': r['t'], 'bid': r['bid'], 'ask': r['ask'],
                                   'mid': (r['bid'] + r['ask']) / 2, 'rest': True})
    for v in pm.values():
        v.sort(key=lambda x: x['t'])
    return meta, af, pm, notes


def grid(series: list[dict], t0: float, t1: float, key: str = 'mid') -> list[float | None]:
    """Forward-fill onto a 1-second grid."""
    out, i, last = [], 0, None
    for t in range(int(t0), int(t1) + 1):
        while i < len(series) and series[i]['t'] <= t:
            last = series[i][key]
            i += 1
        out.append(last)
    return out


def map_sides(meta: dict, af: list, pm: dict) -> dict[str, str] | None:
    """token -> Home/Draw/Away, derived from price agreement. Fails closed."""
    toks = meta['tokens']
    draw = [t for t, m in toks.items() if 'draw' in (m['slug'] or '')]
    wins = [t for t, m in toks.items() if t not in draw]
    if len(draw) != 1 or len(wins) != 2:
        return None
    mid = {}
    for t in toks:
        s = pm.get(t) or []
        if not s:
            return None
        mid[t] = sum(x['mid'] for x in s[:40]) / len(s[:40])
    pref = af[0]['p']
    best, score = None, None
    for home, away in ((wins[0], wins[1]), (wins[1], wins[0])):
        err = (abs(mid[home] - pref['Home']) + abs(mid[away] - pref['Away'])
               + abs(mid[draw[0]] - pref['Draw']))
        if score is None or err < score:
            best, score = ({home: 'Home', away: 'Away', draw[0]: 'Draw'}, err)
            alt = None if score is None else err
    # ambiguity check: the two orientations must not fit equally well
    errs = []
    for home, away in ((wins[0], wins[1]), (wins[1], wins[0])):
        errs.append(abs(mid[home] - pref['Home']) + abs(mid[away] - pref['Away']))
    if abs(errs[0] - errs[1]) < 0.04:
        return None
    return best


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 8:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def diffs(g: list[float | None], win: int) -> list[float | None]:
    return [None if (g[i] is None or g[i - win] is None) else g[i] - g[i - win]
            for i in range(len(g))] if len(g) > win else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--max-lag', type=int, default=90)
    ap.add_argument('--diff-window', type=int, default=10)
    ap.add_argument('--jump-pp', type=float, default=2.0)
    a = ap.parse_args()

    meta, af, pm, notes = load(a.path)
    if not meta or not af:
        raise SystemExit('nothing usable in file')
    print(f'== {meta.get("title")}  ({meta.get("slug")})')
    print(f'   af polls with a 1X2 book: {len(af)}   '
          f'window: {af[-1]["t"] - af[0]["t"]:.0f}s   '
          f'clock {af[0]["sec"]} -> {af[-1]["sec"]}')

    # --- resolution ceiling: how often does the sharp feed actually change?
    ups = [r['update'] for r in af]
    changes = [af[i]['t'] for i in range(1, len(af)) if ups[i] != ups[i - 1]]
    gaps = [round(changes[i] - changes[i - 1]) for i in range(1, len(changes))]
    gaps.sort()
    if gaps:
        med = gaps[len(gaps) // 2]
        print(f'   sharp feed refreshes: {len(changes)} distinct updates, '
              f'gap median {med}s  min {gaps[0]}s  max {gaps[-1]}s')
        print(f'   >> RESOLUTION CEILING: lags below ~{max(med, 10)}s are not '
              f'detectable with this sampling.')
    # price movement on the sharp side at all?
    movers = sum(1 for i in range(1, len(af))
                 if any(abs(af[i]['p'][o] - af[i - 1]['p'][o]) > 0.002 for o in OUTCOMES))
    print(f'   sharp polls where a de-vigged prob moved >0.2pp: {movers}/{len(af) - 1}')

    sides = map_sides(meta, af, pm)
    if not sides:
        raise SystemExit('side mapping ambiguous — refusing to report a signed lag')
    for t, s in sides.items():
        print(f'   {s:5s} <- {meta["tokens"][t]["q"]}')

    t0 = max(af[0]['t'], min(v[0]['t'] for v in pm.values() if v))
    t1 = min(af[-1]['t'], max(v[-1]['t'] for v in pm.values() if v))
    n = int(t1 - t0)
    print(f'   overlapping window: {n}s')

    af_g = {o: grid([{'t': r['t'], 'mid': r['p'][o]} for r in af], t0, t1) for o in OUTCOMES}
    pm_g = {sides[t]: grid(pm[t], t0, t1) for t in sides if pm.get(t)}

    # --- level agreement
    print('\n-- level agreement (PM mid vs de-vigged sharp)')
    for o in OUTCOMES:
        pairs = [(pm_g[o][i], af_g[o][i]) for i in range(n)
                 if pm_g.get(o) and pm_g[o][i] is not None and af_g[o][i] is not None]
        if not pairs:
            continue
        d = [(p - q) * 100 for p, q in pairs]
        print(f'   {o:5s} mean PM-sharp {sum(d)/len(d):+.2f}pp   '
              f'mean|d| {sum(abs(x) for x in d)/len(d):.2f}pp   n={len(d)}')

    # --- cross-correlation of changes
    print(f'\n-- cross-correlation of {a.diff_window}s changes '
          f'(lag>0 = PM moves AFTER the sharp feed)')
    for o in OUTCOMES:
        if o not in pm_g:
            continue
        dp, da = diffs(pm_g[o], a.diff_window), diffs(af_g[o], a.diff_window)
        best = []
        for lag in range(-a.max_lag, a.max_lag + 1):
            xs, ys = [], []
            for i in range(len(dp)):
                j = i - lag
                if 0 <= j < len(da) and dp[i] is not None and da[j] is not None:
                    xs.append(dp[i])
                    ys.append(da[j])
            r = pearson(xs, ys)
            if r is not None:
                best.append((r, lag, len(xs)))
        if not best:
            print(f'   {o:5s} no overlapping variation')
            continue
        best.sort(reverse=True)
        r, lag, nn = best[0]
        zero = next((x for x in best if x[1] == 0), None)
        print(f'   {o:5s} peak r={r:+.3f} at lag {lag:+d}s (n={nn})'
              + (f'   r@0={zero[0]:+.3f}' if zero else ''))

    # --- event study: who moves first on a real jump
    print(f'\n-- event study: sharp jumps >= {a.jump_pp}pp')
    ev = 0
    for i in range(1, len(af)):
        for o in OUTCOMES:
            d = (af[i]['p'][o] - af[i - 1]['p'][o]) * 100
            if abs(d) < a.jump_pp or o not in pm_g:
                continue
            ev += 1
            k = int(af[i]['t'] - t0)
            before = pm_g[o][max(0, k - 30)]
            at = pm_g[o][min(n - 1, k)]
            after = pm_g[o][min(n - 1, k + 30)]
            if before is None or at is None or after is None:
                continue
            print(f'   t={af[i]["sec"]} {o} sharp {d:+.1f}pp | PM '
                  f'-30s {before*100:.1f} -> 0s {at*100:.1f} -> +30s {after*100:.1f} '
                  f'(pre {(at-before)*100:+.1f}pp, post {(after-at)*100:+.1f}pp)')
    if not ev:
        print('   none — the sharp feed never jumped that far in this window')

    # --- reverse direction: PM jumps first, does the sharp feed follow?
    print(f'\n-- event study: PM jumps >= {a.jump_pp}pp in {a.diff_window}s')
    rev = 0
    for o in OUTCOMES:
        if o not in pm_g:
            continue
        last = -999
        for i in range(a.diff_window, n):
            x, y = pm_g[o][i], pm_g[o][i - a.diff_window]
            if x is None or y is None:
                continue
            d = (x - y) * 100
            if abs(d) < a.jump_pp or i - last < 60:
                continue
            last = i
            rev += 1
            sb, sa = af_g[o][max(0, i - 60)], af_g[o][min(n - 1, i + 60)]
            s0 = af_g[o][i]
            if sb is None or sa is None or s0 is None:
                continue
            print(f'   +{i}s {o} PM {d:+.1f}pp | sharp -60s {sb*100:.1f} -> '
                  f'0s {s0*100:.1f} -> +60s {sa*100:.1f} '
                  f'(pre {(s0-sb)*100:+.1f}pp, post {(sa-s0)*100:+.1f}pp)')
    if not rev:
        print('   none')

    # --- goals: the one information event both sides must price
    goals = []
    for i in range(1, len(af)):
        if af[i]['goals'] != af[i - 1]['goals']:
            goals.append(af[i])
    print(f'\n-- goals seen by the score feed: {len(goals)}')
    for g in goals:
        k = int(g['t'] - t0)
        for o in OUTCOMES:
            if o not in pm_g:
                continue
            row = [pm_g[o][min(max(0, k + dt), n - 1)] for dt in (-60, -20, 0, 20, 60)]
            if any(v is None for v in row):
                continue
            print(f'   {g["sec"]} {g["goals"]} {o:5s} PM ' +
                  ' -> '.join(f'{v*100:.1f}' for v in row) + '  (-60,-20,0,+20,+60s)')

    print(f'\n-- recorder health: {len(notes)} notes')
    kinds = defaultdict(int)
    for x in notes:
        if 'error' in x:
            kinds[(x.get('src'), 'error')] += 1
        elif 'connected' in x:
            kinds[(x.get('src'), 'connected')] += 1
        elif x.get('empty'):
            kinds[(x.get('src'), 'empty response')] += 1
        else:
            kinds[(x.get('src'), 'other')] += 1
    for k, v in sorted(kinds.items(), key=lambda z: str(z[0])):
        print(f'   {k[0]} {k[1]}: {v}')
    print('\n⚠️  the sharp side is api-football\'s ANONYMOUS live aggregate — '
          'no bookmaker attribution. This is not Betfair.')


if __name__ == '__main__':
    main()
