#!/usr/bin/env python3
"""
Stage N: Transfermarkt — every game of ~45 competitions with its context, and
what each starting XI was worth on the day.

Source: dcaribou/transfermarkt-datasets (CC0), published as CSVs in a public
bucket — no account, no key:
    https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/<table>.csv.gz

Loads:
  tm_games            89k games 2006 → mid-2026: domestic leagues, cups and
                      European competitions, with referee, attendance,
                      stadium, managers and formations.
  tm_lineup_features  per game and club: the starting XI's market value (each
                      player's last valuation ON OR BEFORE the game date, so no
                      lookahead), the bench's, and how many starters changed
                      since the club's previous game in ANY competition.
  tm_club_map         Transfermarkt club id → our team, learned by the linker.
  match_context       attendance, venue, managers, formations, and the referee
                      where Football-Data has none (it only covers England and
                      Scotland), for every linked match.

Linking a TM game to `matches` needs the same league, the date within a day,
the same final score and both names through `fixture_match.team_score`, unique
best only. Then Transfermarkt's club ids do the rest: an id proven by two name
matches pairs the games names alone cannot reach.

Usage:
    python stage_n_transfermarkt.py              # download (cached), load, link, features
    python stage_n_transfermarkt.py --refresh    # re-download first
    python stage_n_transfermarkt.py --link-only
    python stage_n_transfermarkt.py --report
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / '.env')
from db_pool import ingest_url  # noqa: E402

sys.path.insert(0, str(HERE.parent / 'agent'))
from fixture_match import team_score  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('stage_n')

BASE = 'https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/{t}.csv.gz'
CACHE = HERE / '.cache' / 'transfermarkt'
TABLES = ['games', 'competitions', 'game_lineups', 'player_valuations']

# Transfermarkt competition -> our leagues.code
TM_TO_CODE = {
    'GB1': 'ENG-PR', 'ES1': 'ESP-LL', 'IT1': 'ITA-SA', 'L1': 'GER-BL1', 'FR1': 'FRA-L1',
    'NL1': 'NED-ED', 'PO1': 'POR-PL', 'BE1': 'BEL-JPL', 'TR1': 'TUR-SL', 'GR1': 'GRE-SL',
    'SC1': 'SCO-PR', 'RU1': 'RUS-PL', 'DK1': 'DEN-SL', 'A1': 'AUT-BL', 'C1': 'SUI-SL',
    'NO1': 'NOR-EL', 'SE1': 'SWE-AL', 'PL1': 'POL-EK', 'RO1': 'ROU-L1', 'JAP1': 'JPN-J1',
    'MLS1': 'USA-MLS', 'BRA1': 'BRA-SA', 'ARG1': 'ARG-PD', 'MEX1': 'MEX-LMX',
}

DATE_WINDOW = timedelta(days=1)
MIN_SIDE = 0.6


def download(refresh: bool) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for t in TABLES:
        f = CACHE / f'{t}.csv.gz'
        if f.exists() and not refresh:
            continue
        log.info(f'downloading {t}')
        with requests.get(BASE.format(t=t), stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(f, 'wb') as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)


def _int(v):
    try:
        return int(float(v)) if pd.notna(v) and str(v).strip() != '' else None
    except ValueError:
        return None


def _txt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = ' '.join(str(v).split())
    return s or None


def load_games(conn) -> pd.DataFrame:
    g = pd.read_csv(CACHE / 'games.csv.gz', dtype=str, keep_default_na=False)
    comp = pd.read_csv(CACHE / 'competitions.csv.gz', dtype=str, keep_default_na=False)
    ctype = dict(zip(comp['competition_id'], comp['type']))
    g = g[g['date'].str.len() >= 10]
    rows = []
    for x in g.to_dict('records'):
        rows.append((
            int(x['game_id']), x['competition_id'], x.get('competition_type') or ctype.get(x['competition_id']),
            _int(x['season']), _txt(x['round']), x['date'][:10],
            _int(x['home_club_id']), _int(x['away_club_id']),
            _txt(x['home_club_name']), _txt(x['away_club_name']),
            _int(x['home_club_goals']), _int(x['away_club_goals']),
            _int(x['home_club_position']), _int(x['away_club_position']),
            _txt(x['home_club_manager_name']), _txt(x['away_club_manager_name']),
            _txt(x['home_club_formation']), _txt(x['away_club_formation']),
            _txt(x['stadium']), _int(x['attendance']), _txt(x['referee']), _txt(x['aggregate']),
        ))
    cols = ('game_id, competition_id, competition_type, season, round, game_date, home_club_id, '
            'away_club_id, home_club_name, away_club_name, home_goals, away_goals, home_position, '
            'away_position, home_manager, away_manager, home_formation, away_formation, stadium, '
            'attendance, referee, aggregate')
    upd = ', '.join(f'{c} = EXCLUDED.{c}' for c in cols.split(', ') if c != 'game_id')
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur, f'INSERT INTO tm_games ({cols}) VALUES %s ON CONFLICT (game_id) DO UPDATE SET {upd}',
        rows, page_size=2000)
    conn.commit()
    log.info(f'tm_games: {len(rows)} games upserted')
    return g


def link(conn) -> None:
    cur = conn.cursor()
    cur.execute('SELECT code, id FROM leagues')
    league_id = dict(cur.fetchall())
    cur.execute('SELECT id, canonical_name FROM teams UNION ALL SELECT team_id, alias FROM team_aliases')
    names = defaultdict(set)
    for tid, n in cur.fetchall():
        if n:
            names[tid].add(n)
    votes: dict[int, Counter] = defaultdict(Counter)    # tm club id -> our team ids
    tm_name = {}
    total = Counter()
    pending = []      # per league: (code, ours_by_score, theirs, matched, used)
    for tm_comp, code in TM_TO_CODE.items():
        if code not in league_id:
            continue
        cur.execute("""SELECT m.id, m.kickoff_utc, m.home_team_id, m.away_team_id, m.home_score, m.away_score
                       FROM matches m JOIN seasons s ON s.id = m.season_id
                       WHERE s.league_id = %s AND m.home_score IS NOT NULL""", (league_id[code],))
        by_score = defaultdict(list)
        for mid, ko, h, a, hs, as_ in cur.fetchall():
            by_score[(hs, as_)].append((mid, ko.date(), h, a))
        cur.execute("""SELECT game_id, game_date, home_club_id, away_club_id, home_club_name, away_club_name,
                              home_goals, away_goals
                       FROM tm_games WHERE competition_id = %s AND home_goals IS NOT NULL""", (tm_comp,))
        theirs = cur.fetchall()
        proposals = []
        for gid, gd, hc, ac, hn, an, hg, ag in theirs:
            tm_name[hc], tm_name[ac] = hn, an
            cands = []
            for mid, d, h, a in by_score.get((hg, ag), ()):
                if abs(d - gd) > DATE_WINDOW:
                    continue
                sh = max((team_score(hn, n) for n in names.get(h, ())), default=0.0)
                if sh < MIN_SIDE:
                    continue
                sa_ = max((team_score(an, n) for n in names.get(a, ())), default=0.0)
                if sa_ < MIN_SIDE:
                    continue
                cands.append(((sh + sa_) / 2, mid, h, a))
            if not cands:
                continue
            cands.sort(key=lambda c: -c[0])
            if len(cands) > 1 and cands[1][0] >= cands[0][0] - 1e-9:
                continue
            proposals.append((cands[0][0], gid, cands[0][1], hc, ac, cands[0][2], cands[0][3]))
        proposals.sort(key=lambda p: -p[0])
        matched, used = {}, set()
        for s, gid, mid, hc, ac, h, a in proposals:
            if gid in matched or mid in used:
                continue
            matched[gid] = (mid, round(s, 3))
            used.add(mid)
            votes[hc][h] += 1
            votes[ac][a] += 1
        pending.append((tm_comp, code, by_score, theirs, matched, used))

    # Club ids pair what names could not. A game with ONE side proven (an id
    # with >= 2 agreeing name matches) whose candidates — same score, within a
    # day, that team on that side — number exactly one is that match, and the
    # other club's id is learned from it ("Atlético de Madrid" never scores
    # against Football-Data's "Ath Madrid"). Repeat while it keeps pairing.
    by_name = {tm_comp: len(matched) for tm_comp, _, _, _, matched, _ in pending}
    while True:
        club_map = {c: next(iter(v)) for c, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}
        added = 0
        for tm_comp, code, by_score, theirs, matched, used in pending:
            for gid, gd, hc, ac, hn, an, hg, ag in theirs:
                if gid in matched:
                    continue
                h, a = club_map.get(hc), club_map.get(ac)
                if h is None and a is None:
                    continue
                cands = [(mid, th, ta) for mid, d, th, ta in by_score.get((hg, ag), ())
                         if mid not in used and abs(d - gd) <= DATE_WINDOW
                         and (h is None or th == h) and (a is None or ta == a)]
                if len(cands) != 1:
                    continue
                mid, th, ta = cands[0]
                matched[gid] = (mid, 1.0)
                used.add(mid)
                votes[hc][th] += 1
                votes[ac][ta] += 1
                added += 1
        if not added:
            break
    club_map = {c: next(iter(v)) for c, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}
    for tm_comp, code, by_score, theirs, matched, used in pending:
        cur.execute('UPDATE tm_games SET match_id = NULL, link_score = NULL WHERE competition_id = %s', (tm_comp,))
        if matched:
            psycopg2.extras.execute_values(
                cur, """UPDATE tm_games g SET match_id = v.m, link_score = v.s
                        FROM (VALUES %s) AS v(g, m, s) WHERE g.game_id = v.g""",
                [(g, m, s) for g, (m, s) in matched.items()], page_size=2000)
        conn.commit()
        total['tm'] += len(theirs); total['linked'] += len(matched)
        log.info(f'link {tm_comp:5s} -> {code:8s} tm {len(theirs):5d}  linked {len(matched):5d} '
                 f'(by name {by_name[tm_comp]}, by club id {len(matched) - by_name[tm_comp]})')

    cur.execute('DELETE FROM tm_club_map')
    psycopg2.extras.execute_values(
        cur, 'INSERT INTO tm_club_map (tm_club_id, team_id, tm_name, n_links) VALUES %s',
        [(c, t, tm_name.get(c), sum(votes[c].values())) for c, t in club_map.items()])
    conn.commit()
    log.info(f'linked {total["linked"]} of {total["tm"]} league games; {len(club_map)} clubs mapped')


def fill_context(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO match_context (match_id, referee, referee_source, attendance, venue,
                                   home_manager, away_manager, home_formation, away_formation, context_source)
        SELECT g.match_id, g.referee, CASE WHEN g.referee IS NOT NULL THEN 'transfermarkt' END,
               g.attendance, g.stadium, g.home_manager, g.away_manager, g.home_formation,
               g.away_formation, 'transfermarkt'
        FROM tm_games g WHERE g.match_id IS NOT NULL
        ON CONFLICT (match_id) DO UPDATE SET
            attendance = EXCLUDED.attendance, venue = EXCLUDED.venue,
            home_manager = EXCLUDED.home_manager, away_manager = EXCLUDED.away_manager,
            home_formation = EXCLUDED.home_formation, away_formation = EXCLUDED.away_formation,
            context_source = 'transfermarkt', updated_at = now(),
            -- Football-Data's referee stays where it exists; TM fills the rest.
            referee = COALESCE(match_context.referee, EXCLUDED.referee),
            referee_source = COALESCE(match_context.referee_source, EXCLUDED.referee_source)""")
    n = cur.rowcount
    conn.commit()
    log.info(f'match_context: {n} rows from Transfermarkt')


def lineup_features(conn) -> None:
    games = pd.read_csv(CACHE / 'games.csv.gz', usecols=['game_id', 'date'], dtype={'game_id': 'int64'})
    games['date'] = pd.to_datetime(games['date'], errors='coerce')
    lu = pd.read_csv(CACHE / 'game_lineups.csv.gz', usecols=['game_id', 'club_id', 'player_id', 'type'],
                     dtype={'game_id': 'int64', 'club_id': 'int64', 'player_id': 'int64', 'type': 'category'})
    lu = lu[lu['type'].isin(['starting_lineup', 'substitutes'])]
    lu = lu.drop_duplicates(['game_id', 'club_id', 'player_id'])
    lu = lu.merge(games, on='game_id', how='inner').dropna(subset=['date'])
    log.info(f'line-up rows: {len(lu)} over {lu["game_id"].nunique()} games')

    val = pd.read_csv(CACHE / 'player_valuations.csv.gz', usecols=['player_id', 'date', 'market_value_in_eur'],
                      dtype={'player_id': 'int64'})
    val['date'] = pd.to_datetime(val['date'], errors='coerce')
    val = val.dropna(subset=['date', 'market_value_in_eur']).sort_values('date')
    lu = lu.sort_values('date')
    # The last valuation ON OR BEFORE the game: a value published after the
    # match would carry the match itself.
    lu = pd.merge_asof(lu, val.rename(columns={'date': 'vdate'}), left_on='date', right_on='vdate',
                       by='player_id', direction='backward')

    st = lu[lu['type'] == 'starting_lineup']
    agg = st.groupby(['game_id', 'club_id']).agg(
        n_starters=('player_id', 'size'),
        n_starters_valued=('market_value_in_eur', 'count'),
        xi_value_eur=('market_value_in_eur', 'sum'),
        date=('date', 'first')).reset_index()
    bench = lu[lu['type'] == 'substitutes'].groupby(['game_id', 'club_id'])['market_value_in_eur'].sum()
    agg = agg.join(bench.rename('bench_value_eur'), on=['game_id', 'club_id'])

    # Rotation: starters who did not start the club's previous game.
    xi = st.groupby(['club_id', 'game_id', 'date'])['player_id'].apply(frozenset).reset_index()
    xi = xi.sort_values(['club_id', 'date', 'game_id'])
    xi['prev_xi'] = xi.groupby('club_id')['player_id'].shift(1)
    xi['prev_game_id'] = xi.groupby('club_id')['game_id'].shift(1)
    xi['prev_date'] = xi.groupby('club_id')['date'].shift(1)
    xi['xi_changes'] = [len(a - b) if isinstance(b, frozenset) else None
                        for a, b in zip(xi['player_id'], xi['prev_xi'])]
    xi['days_since_prev'] = (xi['date'] - xi['prev_date']).dt.days
    agg = agg.merge(xi[['club_id', 'game_id', 'xi_changes', 'prev_game_id', 'days_since_prev']],
                    on=['club_id', 'game_id'], how='left')

    def iv(v):
        return None if pd.isna(v) else int(v)

    rows = [(int(r.game_id), int(r.club_id), iv(r.n_starters), iv(r.n_starters_valued),
             iv(r.xi_value_eur) if r.n_starters_valued else None, iv(r.bench_value_eur),
             iv(r.xi_changes), iv(r.prev_game_id), iv(r.days_since_prev))
            for r in agg.itertuples()]
    cur = conn.cursor()
    cur.execute('SELECT game_id FROM tm_games')
    known = {r[0] for r in cur.fetchall()}
    rows = [r for r in rows if r[0] in known]
    psycopg2.extras.execute_values(
        cur, """INSERT INTO tm_lineup_features (game_id, club_id, n_starters, n_starters_valued,
                    xi_value_eur, bench_value_eur, xi_changes, prev_game_id, days_since_prev)
                VALUES %s ON CONFLICT (game_id, club_id) DO UPDATE SET
                    n_starters = EXCLUDED.n_starters, n_starters_valued = EXCLUDED.n_starters_valued,
                    xi_value_eur = EXCLUDED.xi_value_eur, bench_value_eur = EXCLUDED.bench_value_eur,
                    xi_changes = EXCLUDED.xi_changes, prev_game_id = EXCLUDED.prev_game_id,
                    days_since_prev = EXCLUDED.days_since_prev""", rows, page_size=5000)
    conn.commit()
    log.info(f'tm_lineup_features: {len(rows)} club-games')


def report(conn) -> None:
    cur = conn.cursor()
    cur.execute("""SELECT competition_id, count(*), count(match_id), min(game_date), max(game_date)
                   FROM tm_games WHERE competition_id = ANY(%s) GROUP BY 1 ORDER BY 2 DESC""", (list(TM_TO_CODE),))
    print(f'{"comp":5s} {"games":>6s} {"linked":>7s}  from        to')
    for r in cur.fetchall():
        print(f'{r[0]:5s} {r[1]:6d} {r[2]:7d}  {r[3]}  {r[4]}')
    cur.execute("""SELECT count(*), count(xi_value_eur), count(xi_changes),
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY xi_changes) FROM tm_lineup_features""")
    print('lineup features (rows, valued, with rotation, median changes):', cur.fetchone())
    cur.execute("""SELECT context_source, count(*), count(attendance), count(home_manager), count(referee)
                   FROM match_context GROUP BY 1""")
    print('match_context:', cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--link-only', action='store_true')
    ap.add_argument('--no-lineups', action='store_true')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    conn = psycopg2.connect(ingest_url())
    try:
        if args.report:
            report(conn); return
        if not args.link_only:
            download(args.refresh)
            load_games(conn)
        link(conn)
        fill_context(conn)
        if not args.link_only and not args.no_lineups:
            lineup_features(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
