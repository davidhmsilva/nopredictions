#!/usr/bin/env python3
"""
Stage K: everything Football-Data publishes that Stage A never loaded.

--extra-leagues
    Sixteen countries from football-data.co.uk/new/<CODE>.csv — results and
    closing 1X2 (Pinnacle to 2026-01, Betfair Exchange, market max/average,
    Bet365) from 2012. Twelve are new leagues here. ARG, BRA, MEX and USA
    already hold api-football matches for 2020-25 (Stage G) with NO odds: those
    rows get the odds attached to the existing match, and only the seasons
    Stage G never had (2012-19, 2026) become new matches.

--enrich
    From the 22 Stage A CSVs (Stage A's cache; the live season re-downloaded):
      * Asian handicap line + prices on the existing odds rows — the closing
        line AHCh for PSC/B365/MAX/AVG/BFEX, the earlier AHh for PS/BF.
        `match_odds.ah_*` existed and were empty on all 530k rows.
      * Pre-closing market max/average (MAXO/AVGO, snapshot 'opening'): 1X2,
        O/U 2.5 and AH — Betbrain's until 2018-19, Football-Data's own after.
        Before 2019-20 there was no O/U price in the database at all.
      * xG (HxG/AxG) — Football-Data added it in 2026-27 for 19 of the 22
        divisions. Never overwrites Understat's.
      * Referee (England and Scotland) -> match_context.

Kick-off times in the /new/ files are UK local time (J1's "09:00" on
2026-09-20 is ESPN's 08:00Z), converted here to a real UTC instant. Stage A's
own leagues keep their convention (London clock stored as UTC, see
finding-fd-kickoff-london-time) because this stage only reads their rows back.

Every write goes through the transaction pooler (db_pool.ingest_url): the
session pool's 15 slots belong to the agents.

Usage:
    python stage_k_fd_extra.py --extra-leagues --dry-run
    python stage_k_fd_extra.py --extra-leagues [--countries JPN USA]
    python stage_k_fd_extra.py --enrich [--leagues ENG-PR] [--seasons 2023-24]
    python stage_k_fd_extra.py --report
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / '.env')

from db_pool import ingest_url  # noqa: E402
import stage_a_football_data as sa  # noqa: E402  (CSV download + parsing helpers)

sys.path.insert(0, str(HERE.parent / 'agent'))
from fixture_match import team_score  # noqa: E402

log = logging.getLogger('stage_k')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

LONDON = ZoneInfo('Europe/London')
FD_CACHE = HERE / '.cache' / 'fd'
NEW_BASE = 'https://www.football-data.co.uk/new/{cc}.csv'

# ---------------------------------------------------------------------------
# Extra leagues
# ---------------------------------------------------------------------------

# FD country code -> (country, {FD "League" value, stripped -> our code})
EXTRA = {
    'ARG': ('Argentina', {'Liga Profesional': 'ARG-PD', 'Copa De La Liga Profesional': 'ARG-CLP'}),
    'AUT': ('Austria', {'Bundesliga': 'AUT-BL'}),
    'BRA': ('Brazil', {'Serie A': 'BRA-SA'}),
    'CHN': ('China', {'Super League': 'CHN-CSL'}),
    'DNK': ('Denmark', {'Superliga': 'DEN-SL'}),
    'FIN': ('Finland', {'Veikkausliiga': 'FIN-VL'}),
    'IRL': ('Ireland', {'Premier Division': 'IRL-PD'}),
    'JPN': ('Japan', {'J1 League': 'JPN-J1'}),
    'MEX': ('Mexico', {'Liga MX': 'MEX-LMX'}),
    'NOR': ('Norway', {'Eliteserien': 'NOR-EL'}),
    'POL': ('Poland', {'Ekstraklasa': 'POL-EK'}),
    'ROU': ('Romania', {'Superliga': 'ROU-L1'}),
    'RUS': ('Russia', {'Premier League': 'RUS-PL'}),
    'SWE': ('Sweden', {'Allsvenskan': 'SWE-AL'}),
    # 'Challenge League' rows in SWZ are relegation play-offs: not mapped, skipped.
    'SWZ': ('Switzerland', {'Super League': 'SUI-SL'}),
    'USA': ('USA', {'MLS': 'USA-MLS'}),
}

# Leagues that already hold api-football matches (Stage G). An FD row in one
# of these countries is first matched against them; the code it is searched
# under is the existing league, whatever FD's own label says (Argentina's
# 2020 "cup" seasons sit in Stage G's ARG-PD).
PARTNER = {'ARG': 'ARG-PD', 'BRA': 'BRA-SA', 'MEX': 'MEX-LMX', 'USA': 'USA-MLS'}

EXTRA_BOOKS = {
    'PSC': ('PSCH', 'PSCD', 'PSCA'),
    'MAX': ('MaxCH', 'MaxCD', 'MaxCA'),
    'AVG': ('AvgCH', 'AvgCD', 'AvgCA'),
    'BFEX': ('BFECH', 'BFECD', 'BFECA'),
    'B365': ('B365CH', 'B365CD', 'B365CA'),
}

LINK_WINDOW = timedelta(hours=36)
MIN_SIDE = 0.6


def fnum(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if 1.0 < x < 1000.0 else None     # a decimal price, or nothing


def season_label(code: str, fd_season: str, existing_style_start_year: bool):
    """('2012-13', 2012-07-01, 2013-06-30) for split seasons, ('2012', Jan 1, Dec 31)
    for calendar ones. Stage G labels a season by its start year alone, so a
    league that already holds Stage G seasons keeps that style."""
    s = str(fd_season).strip()
    if '/' in s:
        a = int(s.split('/')[0])
        lab = str(a) if existing_style_start_year else f'{a}-{str(a + 1)[-2:]}'
        return lab, date(a, 7, 1), date(a + 1, 6, 30)
    y = int(s)
    return str(y), date(y, 1, 1), date(y, 12, 31)


def uk_to_utc(d: str, t: str) -> datetime:
    day = datetime.strptime(d.strip(), '%d/%m/%Y').date()
    if t and t.strip():
        hh, mm = t.strip().split(':')[:2]
        local = datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=LONDON)
        return local.astimezone(timezone.utc)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)   # midnight = date only


def download_new(cc: str, refresh: bool) -> pd.DataFrame | None:
    f = FD_CACHE / f'new_{cc}.csv'
    if f.exists() and not refresh:
        return pd.read_csv(f, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    r = requests.get(NEW_BASE.format(cc=cc), timeout=60)   # follows the 302 to football-data.co.uk
    r.raise_for_status()
    head = r.content[:512].lstrip().lower()
    if head.startswith(b'<!doctype') or head.startswith(b'<html'):
        log.warning(f'{cc}: server returned HTML, not a CSV')
        return None
    df = pd.read_csv(BytesIO(r.content), encoding='utf-8-sig', dtype=str, keep_default_na=False)
    if 'Country' not in df.columns or 'Home' not in df.columns:
        log.warning(f'{cc}: unexpected columns {list(df.columns)[:8]}')
        return None
    FD_CACHE.mkdir(parents=True, exist_ok=True)
    f.write_bytes(r.content)
    return df


def team_names(cur) -> dict[int, set[str]]:
    cur.execute("SELECT id, canonical_name FROM teams UNION ALL SELECT team_id, alias FROM team_aliases")
    names = defaultdict(set)
    for tid, n in cur.fetchall():
        if n:
            names[tid].add(n)
    return names


def ingest_extra(conn, cc: str, refresh: bool, dry_run: bool) -> Counter:
    country, league_map = EXTRA[cc]
    stats = Counter()
    df = download_new(cc, refresh)
    if df is None or df.empty:
        log.warning(f'{cc}: no data')
        return stats
    cur = conn.cursor()
    cur.execute('SELECT code, id FROM leagues')
    league_id = dict(cur.fetchall())
    cur.execute('SELECT code, id FROM bookmakers')
    book_id = dict(cur.fetchall())
    names = team_names(cur)
    alias_source = f'fd-{cc.lower()}'
    cur.execute('SELECT alias, team_id FROM team_aliases WHERE source = %s', (alias_source,))
    known_alias = dict(cur.fetchall())

    # Existing matches of the partner league (Stage G), by score.
    partner = PARTNER.get(cc)
    existing = defaultdict(list)          # (hs, as) -> [(id, ko, h, a)]
    window = None
    league_teams: dict[str, int] = {}     # exact name -> team id, teams of the partner league
    if partner:
        cur.execute("""SELECT m.id, m.kickoff_utc, m.home_team_id, m.away_team_id, m.home_score, m.away_score,
                              m.fd_source
                       FROM matches m JOIN seasons s ON s.id = m.season_id
                       WHERE s.league_id = %s AND m.home_score IS NOT NULL""", (league_id[partner],))
        kos = []
        tids = set()
        for mid, ko, h, a, hs, as_, src in cur.fetchall():
            # Rows this stage inserted on an earlier run are matched like any
            # other (that is what keeps a re-run from duplicating them), but
            # only Stage G's own rows (fd_source NULL) define the overlap
            # window: counting ours would stretch it over 2012-2026 and every
            # new result would be skipped as "unmatched in overlap".
            existing[(hs, as_)].append((mid, ko, h, a))
            if src is None:
                kos.append(ko)
            tids.update((h, a))
        if kos:
            window = (min(kos) - timedelta(days=7), max(kos) + timedelta(days=7))
        for tid in tids:
            for n in names.get(tid, ()):
                league_teams.setdefault(n.strip().lower(), tid)

    rows = []
    for r in df.to_dict('records'):
        code = league_map.get(str(r.get('League', '')).strip())
        if code is None:
            stats['skipped_other_competition'] += 1
            continue
        hg = int(float(r['HG'])) if str(r.get('HG', '')).strip() != '' else None
        ag = int(float(r['AG'])) if str(r.get('AG', '')).strip() != '' else None
        if hg is None or ag is None:
            stats['skipped_unplayed'] += 1
            continue
        try:
            ko = uk_to_utc(r['Date'], r.get('Time', ''))
        except (ValueError, KeyError):
            stats['skipped_bad_date'] += 1
            continue
        home, away = str(r['Home']).strip(), str(r['Away']).strip()
        if not home or not away or home == away:
            stats['skipped_bad_teams'] += 1
            continue
        rows.append({'code': code, 'season': str(r['Season']).strip(), 'ko': ko,
                     'home': home, 'away': away, 'hg': hg, 'ag': ag,
                     'odds': {bm: tuple(fnum(r.get(c)) for c in cols) for bm, cols in EXTRA_BOOKS.items()}})

    # 1. Match against Stage G where it exists. Unique best, then one-to-one.
    proposals = []
    for i, x in enumerate(rows):
        cands = []
        for mid, mko, h, a in existing.get((x['hg'], x['ag']), ()):
            if abs(mko - x['ko']) > LINK_WINDOW:
                continue
            sh = max((team_score(x['home'], n) for n in names.get(h, ())), default=0.0)
            if sh < MIN_SIDE:
                continue
            sa_ = max((team_score(x['away'], n) for n in names.get(a, ())), default=0.0)
            if sa_ < MIN_SIDE:
                continue
            cands.append(((sh + sa_) / 2, mid, h, a, mko))
        if not cands:
            continue
        cands.sort(key=lambda c: -c[0])
        if len(cands) > 1 and cands[1][0] >= cands[0][0] - 1e-9:
            stats['overlap_tie'] += 1
            continue
        proposals.append((cands[0][0], i) + cands[0][1:])
    proposals.sort(key=lambda p: -p[0])
    matched = {}             # row index -> (match_id, ko)
    used = set()
    votes: dict[str, Counter] = defaultdict(Counter)
    for s, i, mid, h, a, mko in proposals:
        if mid in used or i in matched:
            continue
        used.add(mid)
        matched[i] = (mid, mko)
        votes[rows[i]['home']][h] += 1
        votes[rows[i]['away']][a] += 1
    stats['matched_by_name'] = len(matched)

    # 1b. Pairing. A row whose one side is already proven (>= 2 agreeing
    # name matches) and whose candidates within the window, at the same score,
    # with that team on that side, number exactly ONE, is that match. This is
    # how spellings no scorer should reach get learned — "Athletico-PR" against
    # "Atletico Paranaense" scores 0.00, "UNAM Pumas" against "U.N.A.M. - Pumas"
    # 0.20 — from the fixture list itself rather than by loosening the scorer.
    # Repeat while it keeps finding pairs: each one proves another spelling.
    while True:
        vote_map = {n: next(iter(v)) for n, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}
        added = 0
        for i, x in enumerate(rows):
            if i in matched:
                continue
            hk, ak = vote_map.get(x['home']), vote_map.get(x['away'])
            if hk is None and ak is None:
                continue
            cands = [(mid, mko, h, a) for mid, mko, h, a in existing.get((x['hg'], x['ag']), ())
                     if mid not in used and abs(mko - x['ko']) <= LINK_WINDOW
                     and (hk is None or h == hk) and (ak is None or a == ak)]
            if len(cands) != 1:
                continue
            mid, mko, h, a = cands[0]
            matched[i] = (mid, mko)
            used.add(mid)
            votes[x['home']][h] += 1
            votes[x['away']][a] += 1
            added += 1
        if not added:
            break
    stats['matched_by_pairing'] = len(matched) - stats['matched_by_name']
    stats['matched_existing'] = len(matched)
    vote_map = {n: next(iter(v)) for n, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}

    # 2. Decide what becomes a new match.
    inserts = []
    for i, x in enumerate(rows):
        if i in matched:
            continue
        if partner and window and window[0] <= x['ko'] <= window[1] and x['code'] == partner:
            # Inside Stage G's own span and still unmatched: a spelling we could
            # not prove, not a missing match. A duplicate is worse than a gap.
            stats['skipped_unmatched_in_overlap'] += 1
            if stats['skipped_unmatched_in_overlap'] <= 5:
                log.info(f'  {cc} unmatched in overlap: {x["ko"]:%Y-%m-%d} {x["home"]} {x["hg"]}-{x["ag"]} {x["away"]}')
            continue
        inserts.append(i)

    if dry_run:
        stats['would_insert'] = len(inserts)
        stats['odds_rows'] = sum(1 for i, x in enumerate(rows) if i in matched or i in set(inserts)
                                 for bm, o in x['odds'].items() if all(o))
        return stats

    log_id = sa.log_start(cur, 'football-data-new', f'new/{cc}')
    conn.commit()

    # 3. Teams for the inserts.
    def resolve(name: str) -> int:
        if name in vote_map:
            tid = vote_map[name]
        elif name in known_alias:
            return known_alias[name]
        elif name.lower() in league_teams:
            tid = league_teams[name.lower()]
        else:
            cur.execute("SELECT id FROM teams WHERE canonical_name = %s AND country = %s", (name, country))
            got = cur.fetchone()
            if got:
                tid = got[0]
            else:
                cur.execute('INSERT INTO teams (canonical_name, country) VALUES (%s, %s) RETURNING id',
                            (name, country))
                tid = cur.fetchone()[0]
                stats['teams_created'] += 1
        cur.execute("""INSERT INTO team_aliases (team_id, source, alias) VALUES (%s, %s, %s)
                       ON CONFLICT (source, alias) DO NOTHING""", (tid, alias_source, name))
        known_alias[name] = tid
        return tid

    # The aliases learned from the overlap are recorded even when no insert needs them.
    for n, tid in vote_map.items():
        if n not in known_alias:
            cur.execute("""INSERT INTO team_aliases (team_id, source, alias) VALUES (%s, %s, %s)
                           ON CONFLICT (source, alias) DO NOTHING""", (tid, alias_source, n))
            known_alias[n] = tid

    season_ids: dict[tuple, int] = {}

    def season_id(code: str, fd_season: str) -> int:
        key = (code, fd_season)
        if key in season_ids:
            return season_ids[key]
        lab, start, end = season_label(code, fd_season, existing_style_start_year=(code == partner))
        cur.execute('SELECT id FROM seasons WHERE league_id = %s AND label = %s', (league_id[code], lab))
        got = cur.fetchone()
        if got:
            sid = got[0]
        else:
            cur.execute('INSERT INTO seasons (league_id, label, start_date, end_date) VALUES (%s, %s, %s, %s) RETURNING id',
                        (league_id[code], lab, start, end))
            sid = cur.fetchone()[0]
        season_ids[key] = sid
        return sid

    new_ids = {}
    vals = []
    for i in inserts:
        x = rows[i]
        h, a = resolve(x['home']), resolve(x['away'])
        if h == a:
            stats['skipped_same_team'] += 1
            continue
        vals.append((season_id(x['code'], x['season']), h, a, x['ko'], x['hg'], x['ag'], i))
    if vals:
        got = psycopg2.extras.execute_values(
            cur,
            """INSERT INTO matches (season_id, home_team_id, away_team_id, kickoff_utc, status,
                                    home_score, away_score, fd_source)
               SELECT v.s, v.h, v.a, v.ko, 'finished', v.hs, v.as_, 'football-data-new'
               FROM (VALUES %s) AS v(s, h, a, ko, hs, as_, i)
               ON CONFLICT (season_id, home_team_id, away_team_id, kickoff_utc)
               DO UPDATE SET home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score
               RETURNING id, season_id, home_team_id, away_team_id, kickoff_utc""",
            [v[:6] + (v[6],) for v in vals],
            template='(%s::int, %s::int, %s::int, %s::timestamptz, %s::smallint, %s::smallint, %s::int)',
            page_size=1000, fetch=True)
        by_key = {(s, h, a, ko): mid for mid, s, h, a, ko in got}
        for s, h, a, ko, hs, as_, i in vals:
            mid = by_key.get((s, h, a, ko))
            if mid:
                new_ids[i] = (mid, ko)
    stats['inserted'] = len(new_ids)

    # 4. Odds on every row that has a match now.
    odds = []
    for i, x in enumerate(rows):
        hit = matched.get(i) or new_ids.get(i)
        if not hit:
            continue
        mid, mko = hit
        for bm, (ho, do, ao) in x['odds'].items():
            if ho and do and ao and bm in book_id:
                odds.append((mid, book_id[bm], mko, ho, do, ao))
    if odds:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO match_odds (match_id, bookmaker_id, observed_at, snapshot_type,
                                       home_odds, draw_odds, away_odds)
               SELECT v.m, v.b, v.t, 'closing', v.h, v.d, v.a FROM (VALUES %s) AS v(m, b, t, h, d, a)
               ON CONFLICT (match_id, bookmaker_id, snapshot_type, observed_at)
               DO UPDATE SET home_odds = EXCLUDED.home_odds, draw_odds = EXCLUDED.draw_odds,
                             away_odds = EXCLUDED.away_odds""",
            odds, template='(%s::int, %s::int, %s::timestamptz, %s::numeric, %s::numeric, %s::numeric)',
            page_size=2000)
    stats['odds_rows'] = len(odds)
    sa.log_finish(cur, log_id, stats['inserted'] + stats['matched_existing'])
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Enrichment of the 22 Stage A leagues
# ---------------------------------------------------------------------------

AH_CLOSING = {'PSC': ('PCAHH', 'PCAHA'), 'B365': ('B365CAHH', 'B365CAHA'),
              'MAX': ('MaxCAHH', 'MaxCAHA'), 'AVG': ('AvgCAHH', 'AvgCAHA'),
              'BFEX': ('BFECAHH', 'BFECAHA')}
AH_EARLY = {'PS': ('PAHH', 'PAHA'), 'BF': ('BFEAHH', 'BFEAHA')}

# Pre-closing aggregates: Football-Data's own Max/Avg from 2019-20, Betbrain's
# before. First column set present on the row wins.
PRE = {
    'MAXO': {'1x2': [('MaxH', 'MaxD', 'MaxA'), ('BbMxH', 'BbMxD', 'BbMxA')],
             'ou': [('Max>2.5', 'Max<2.5'), ('BbMx>2.5', 'BbMx<2.5')],
             'ah': [('AHh', 'MaxAHH', 'MaxAHA'), ('BbAHh', 'BbMxAHH', 'BbMxAHA')]},
    'AVGO': {'1x2': [('AvgH', 'AvgD', 'AvgA'), ('BbAvH', 'BbAvD', 'BbAvA')],
             'ou': [('Avg>2.5', 'Avg<2.5'), ('BbAv>2.5', 'BbAv<2.5')],
             'ah': [('AHh', 'AvgAHH', 'AvgAHA'), ('BbAHh', 'BbAvAHH', 'BbAvAHA')]},
}


def fline(v):
    """An AH line may be 0 or negative, unlike a price."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def ah_line(v):
    """A handicap line is a multiple of 0.25 inside ±10. Football-Data carries
    typos — 1314_SC2.csv has BbAHh = -275 for -2.75 — so anything else is
    dropped rather than stored as a line nobody quoted."""
    x = fline(v)
    if x is None or abs(x) > 10 or abs(x * 4 - round(x * 4)) > 1e-6:
        return None
    return x


def first_set(row, options, line=False):
    for cols in options:
        if not all(c in row for c in cols):
            continue
        if line:
            vals = (ah_line(row[cols[0]]),) + tuple(fnum(row[c]) for c in cols[1:])
        else:
            vals = tuple(fnum(row[c]) for c in cols)
        if all(v is not None for v in vals):
            return vals
    return None


def enrich(conn, leagues: list[str], seasons: list[str], dry_run: bool) -> Counter:
    cur = conn.cursor()
    cur.execute('SELECT code, id FROM leagues')
    league_id = dict(cur.fetchall())
    cur.execute('SELECT code, id FROM bookmakers')
    book_id = dict(cur.fetchall())
    cur.execute("SELECT lower(alias), team_id FROM team_aliases WHERE source = 'football-data'")
    fd_team = dict(cur.fetchall())
    cur.execute('SELECT league_id, label, id FROM seasons')
    season_id = {(l, lab): sid for l, lab, sid in cur.fetchall()}

    today = date.today()
    live = f'{today.year if today.month >= 7 else today.year - 1}-'
    total = Counter()
    for lg in leagues:
        lid = league_id[lg]
        cur.execute("""SELECT m.id, m.season_id, m.home_team_id, m.away_team_id, m.kickoff_utc
                       FROM matches m JOIN seasons s ON s.id = m.season_id WHERE s.league_id = %s""", (lid,))
        by_key = {(s, h, a, ko.astimezone(timezone.utc).replace(tzinfo=None)): mid
                  for mid, s, h, a, ko in cur.fetchall()}
        per = Counter()
        for season in seasons:
            sid = season_id.get((lid, season))
            if sid is None:
                continue
            df = sa.download_csv(sa.LEAGUES[lg], season, FD_CACHE, refresh=season.startswith(live))
            if df is None or df.empty:
                continue
            ah, pre, xg, ref = [], [], [], []
            for _, row in df.iterrows():
                if pd.isna(row.get('HomeTeam')) or pd.isna(row.get('AwayTeam')):
                    continue
                ko = sa.parse_kickoff(row)
                h = fd_team.get(str(row['HomeTeam']).strip().lower())
                a = fd_team.get(str(row['AwayTeam']).strip().lower())
                mid = by_key.get((sid, h, a, ko)) if (ko and h and a) else None
                if mid is None:
                    per['row_without_match'] += 1
                    continue
                per['rows'] += 1
                line_c = ah_line(row['AHCh']) if 'AHCh' in row else None
                if line_c is not None:
                    for bm, (ch, ca) in AH_CLOSING.items():
                        if ch in row and bm in book_id:
                            ho, ao = fnum(row[ch]), fnum(row[ca])
                            if ho and ao:
                                ah.append((mid, book_id[bm], line_c, ho, ao))
                line_e = ah_line(row['AHh']) if 'AHh' in row else None
                if line_e is not None:
                    for bm, (ch, ca) in AH_EARLY.items():
                        if ch in row and bm in book_id:
                            ho, ao = fnum(row[ch]), fnum(row[ca])
                            if ho and ao:
                                ah.append((mid, book_id[bm], line_e, ho, ao))
                for bm, spec in PRE.items():
                    x12 = first_set(row, spec['1x2'])
                    ou = first_set(row, spec['ou'])
                    ahs = first_set(row, spec['ah'], line=True)
                    if not (x12 or ou or ahs):
                        continue
                    pre.append((mid, book_id[bm], ko,
                                *(x12 or (None, None, None)), *(ou or (None, None)),
                                *(ahs or (None, None, None))))
                if 'HxG' in row:
                    hx, ax = fline(row['HxG']), fline(row.get('AxG'))
                    if hx is not None and ax is not None:
                        xg.append((mid, hx, ax))
                if 'Referee' in row and not pd.isna(row['Referee']) and str(row['Referee']).strip():
                    ref.append((mid, ' '.join(str(row['Referee']).split())))
            per['ah'] += len(ah); per['pre'] += len(pre); per['xg'] += len(xg); per['ref'] += len(ref)
            if dry_run:
                continue
            if ah:
                psycopg2.extras.execute_values(
                    cur,
                    """UPDATE match_odds mo SET ah_line = v.l, ah_home_odds = v.h, ah_away_odds = v.a
                       FROM (VALUES %s) AS v(m, b, l, h, a)
                       WHERE mo.match_id = v.m AND mo.bookmaker_id = v.b AND mo.snapshot_type = 'closing'""",
                    ah, template='(%s::int, %s::int, %s::numeric, %s::numeric, %s::numeric)', page_size=2000)
            if pre:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO match_odds (match_id, bookmaker_id, observed_at, snapshot_type,
                           home_odds, draw_odds, away_odds, over_2_5_odds, under_2_5_odds,
                           ah_line, ah_home_odds, ah_away_odds)
                       SELECT v.m, v.b, v.t, 'opening', v.h, v.d, v.a, v.o, v.u, v.l, v.ah, v.aa
                       FROM (VALUES %s) AS v(m, b, t, h, d, a, o, u, l, ah, aa)
                       ON CONFLICT (match_id, bookmaker_id, snapshot_type, observed_at) DO UPDATE SET
                           home_odds = EXCLUDED.home_odds, draw_odds = EXCLUDED.draw_odds,
                           away_odds = EXCLUDED.away_odds, over_2_5_odds = EXCLUDED.over_2_5_odds,
                           under_2_5_odds = EXCLUDED.under_2_5_odds, ah_line = EXCLUDED.ah_line,
                           ah_home_odds = EXCLUDED.ah_home_odds, ah_away_odds = EXCLUDED.ah_away_odds""",
                    pre, template='(%s::int, %s::int, %s::timestamp, %s::numeric, %s::numeric, %s::numeric, '
                                  '%s::numeric, %s::numeric, %s::numeric, %s::numeric, %s::numeric)',
                    page_size=2000)
            if xg:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO match_stats (match_id, home_xg, away_xg, xg_source)
                       SELECT v.m, v.h, v.a, 'football-data' FROM (VALUES %s) AS v(m, h, a)
                       ON CONFLICT (match_id) DO UPDATE SET home_xg = EXCLUDED.home_xg,
                           away_xg = EXCLUDED.away_xg, xg_source = 'football-data'
                       WHERE match_stats.xg_source IS NULL OR match_stats.xg_source = 'football-data'""",
                    xg, template='(%s::int, %s::numeric, %s::numeric)', page_size=2000)
            if ref:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO match_context (match_id, referee, referee_source)
                       SELECT v.m, v.r, 'football-data' FROM (VALUES %s) AS v(m, r)
                       ON CONFLICT (match_id) DO UPDATE SET referee = EXCLUDED.referee,
                           referee_source = 'football-data', updated_at = now()
                       WHERE match_context.referee_source IS NULL
                          OR match_context.referee_source = 'football-data'""",
                    ref, template='(%s::int, %s::text)', page_size=2000)
            conn.commit()
        log.info(f'enrich {lg:8s} rows {per["rows"]:6d} (no match {per["row_without_match"]:4d})  '
                 f'AH {per["ah"]:6d}  pre-close {per["pre"]:6d}  xG {per["xg"]:5d}  referee {per["ref"]:5d}')
        total.update(per)
    return total


def report(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT l.code, count(DISTINCT m.id), min(m.kickoff_utc)::date, max(m.kickoff_utc)::date,
               count(DISTINCT mo.match_id) FILTER (WHERE b.code = 'PSC'),
               count(DISTINCT mo.match_id) FILTER (WHERE b.code = 'AVG'),
               count(DISTINCT mo.match_id) FILTER (WHERE b.code = 'BFEX')
        FROM leagues l JOIN seasons s ON s.league_id = l.id JOIN matches m ON m.season_id = s.id
        LEFT JOIN match_odds mo ON mo.match_id = m.id LEFT JOIN bookmakers b ON b.id = mo.bookmaker_id
        WHERE l.fd_code LIKE 'new/%%' OR l.code IN ('ARG-PD', 'BRA-SA', 'MEX-LMX', 'USA-MLS')
        GROUP BY 1 ORDER BY 2 DESC""")
    print(f'{"league":9s} {"matches":>8s} {"from":10s} {"to":10s} {"PSC":>6s} {"AVG":>6s} {"BFEX":>6s}')
    for r in cur.fetchall():
        print(f'{r[0]:9s} {r[1]:8d} {r[2]} {r[3]} {r[4]:6d} {r[5]:6d} {r[6]:6d}')
    cur.execute("""SELECT b.code, count(*), count(ah_line), count(over_2_5_odds),
                          min(m.kickoff_utc)::date, max(m.kickoff_utc)::date
                   FROM match_odds mo JOIN bookmakers b ON b.id = mo.bookmaker_id
                   JOIN matches m ON m.id = mo.match_id
                   GROUP BY 1 ORDER BY 1""")
    print('\nbook   rows   with AH   with O/U   from        to')
    for r in cur.fetchall():
        print(f'{r[0]:5s} {r[1]:7d} {r[2]:8d} {r[3]:9d}   {r[4]}  {r[5]}')
    cur.execute("SELECT xg_source, count(*) FROM match_stats WHERE home_xg IS NOT NULL GROUP BY 1")
    print('\nxG by source:', cur.fetchall())
    cur.execute("SELECT referee_source, count(*), count(DISTINCT referee) FROM match_context WHERE referee IS NOT NULL GROUP BY 1")
    print('referees:', cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--extra-leagues', action='store_true')
    ap.add_argument('--countries', nargs='+', default=list(EXTRA))
    ap.add_argument('--enrich', action='store_true')
    ap.add_argument('--leagues', nargs='+', default=list(sa.LEAGUES))
    ap.add_argument('--seasons', nargs='+', default=sa.DEFAULT_SEASONS)
    ap.add_argument('--refresh', action='store_true', help='re-download the /new/ files')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    conn = psycopg2.connect(ingest_url())
    try:
        if args.report:
            report(conn); return
        if args.extra_leagues:
            grand = Counter()
            for cc in args.countries:
                st = ingest_extra(conn, cc, args.refresh, args.dry_run)
                grand.update(st)
                log.info(f'{cc}: {dict(st)}')
            log.info(f'extra leagues TOTAL: {dict(grand)}')
        if args.enrich:
            st = enrich(conn, args.leagues, args.seasons, args.dry_run)
            log.info(f'enrich TOTAL: {dict(st)}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
