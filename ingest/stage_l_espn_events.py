#!/usr/bin/env python3
"""
Stage L: ESPN match timelines — every goal and card with its minute.

Minute-level goal data existed for the Big 5 only (Understat, ending 2024-25),
so the in-play tables were fitted on the leagues Polymarket lists least, and
nothing in the database knew when a red card happened. ESPN's public
scoreboard answers a whole calendar year of a competition in ONE request:

    /apis/site/v2/sports/soccer/<slug>/scoreboard?dates=YYYY&limit=1000

Each event carries `competitions[0].details`: goals (with own-goal and penalty
flags), yellow and red cards, each with `clock.displayValue` — "60'", "45'+2'",
"90'+5'". ~60 competitions × 17 years is ~1,000 requests for the lot.

⚠️ DO NOT SET A USER-AGENT. ESPN's edge serves plain library defaults and 403s
browser-shaped and custom agents (CLAUDE.md, 2026-09-06).
⚠️ A date RANGE (dates=YYYYMMDD-YYYYMMDD) answers 400; a year or a month
(dates=YYYYMM) works. A year that returns `limit` events is split by month.

Raw JSON is cached per slug-year (`.cache/espn/`), so re-parsing costs nothing;
the current year is re-fetched with --refresh-current.

Linking to `matches` needs the date (±36h: Stage A stores London time as UTC),
the SAME final score, and both team names through `fixture_match.team_score`
— unique best only. A missed link is a row kept unlinked, never a wrong one.

Usage:
    python stage_l_espn_events.py --slugs eng.1 --years 2023 --dry-run
    python stage_l_espn_events.py                       # everything, cached
    python stage_l_espn_events.py --refresh-current     # re-fetch this year
    python stage_l_espn_events.py --link-only
    python stage_l_espn_events.py --report
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

from db_pool import ingest_url

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'agent'))
from fixture_match import team_score  # noqa: E402

load_dotenv(HERE / '.env')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('stage_l')

DATABASE_URL = os.getenv('DATABASE_URL')
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard'
CACHE = HERE / '.cache' / 'espn'
LIMIT = 1000
FIRST_YEAR = 2010

# ESPN slug -> our leagues.code. Slugs mapped to None are crawled for their
# own sake (cups, Europe, internationals, leagues we hold no matches for).
SLUGS: dict[str, str | None] = {
    # The 22 Football-Data leagues
    'eng.1': 'ENG-PR', 'eng.2': 'ENG-CH', 'eng.3': 'ENG-L1', 'eng.4': 'ENG-L2',
    'eng.5': 'ENG-CON',
    'sco.1': 'SCO-PR', 'sco.2': 'SCO-CH', 'sco.3': 'SCO-L1', 'sco.4': 'SCO-L2',
    'ger.1': 'GER-BL1', 'ger.2': 'GER-BL2', 'ita.1': 'ITA-SA', 'ita.2': 'ITA-SB',
    'esp.1': 'ESP-LL', 'esp.2': 'ESP-L2', 'fra.1': 'FRA-L1', 'fra.2': 'FRA-L2',
    'ned.1': 'NED-ED', 'bel.1': 'BEL-JPL', 'por.1': 'POR-PL', 'tur.1': 'TUR-SL',
    'gre.1': 'GRE-SL',
    # Americas (Stage G, api-football)
    'usa.1': 'USA-MLS', 'bra.1': 'BRA-SA', 'arg.1': 'ARG-PD', 'mex.1': 'MEX-LMX',
    'col.1': 'COL-PA', 'chi.1': 'CHL-PD', 'conmebol.libertadores': 'CONMEBOL-CL',
    # Football-Data's "extra" leagues (Stage K)
    'jpn.1': 'JPN-J1', 'nor.1': 'NOR-EL', 'swe.1': 'SWE-AL', 'den.1': 'DEN-SL',
    'aut.1': 'AUT-BL', 'sui.1': 'SUI-SL', 'rus.1': 'RUS-PL', 'chn.1': 'CHN-CSL',
    'rou.1': 'ROU-L1', 'pol.1': 'POL-EK', 'irl.1': 'IRL-PD', 'fin.1': 'FIN-VL',
    # Europe, cups — fixtures for rest days / congestion, rows for in-play tables
    'uefa.champions': 'UEFA-UCL', 'uefa.europa': 'UEFA-UEL', 'uefa.europa.conf': 'UEFA-UECL',
    'uefa.champions_qual': None, 'uefa.europa_qual': None, 'uefa.super_cup': None,
    'eng.fa': None, 'eng.league_cup': None, 'esp.copa_del_rey': None,
    'ger.dfb_pokal': None, 'ita.coppa_italia': None, 'fra.coupe_de_france': None,
    'por.taca.portugal': None, 'ned.cup': None, 'sco.tennents': None,
    'conmebol.sudamericana': None, 'concacaf.champions': None,
    # Other leagues Polymarket / Kalshi list
    'usa.usl.1': None, 'bra.2': None, 'arg.2': None, 'aus.1': None, 'ksa.1': None,
    'kor.1': None, 'uru.1': None, 'per.1': None, 'ecu.1': None, 'par.1': None,
    'ven.1': None, 'crc.1': None, 'bol.1': None, 'eng.w.1': None,
    # Internationals
    'fifa.world': 'INT-WC', 'uefa.euro': 'UEFA-EURO', 'uefa.nations': 'INT-UNL',
    'conmebol.america': 'INT-COPA', 'caf.nations': 'INT-AFCON', 'afc.asian.cup': 'INT-AFC',
    'concacaf.gold': None, 'fifa.friendly': 'INT-FR',
    'fifa.worldq.uefa': 'INT-WCQ', 'fifa.worldq.conmebol': 'INT-WCQ',
    'fifa.worldq.concacaf': 'INT-WCQ', 'fifa.worldq.afc': 'INT-WCQ',
    'fifa.worldq.caf': 'INT-WCQ', 'fifa.worldq.ofc': 'INT-WCQ',
}

# Women's competitions are crawled but never linked: our matches are men's.
WOMENS = {'eng.w.1'}

_CLOCK = re.compile(r"(\d+)'?(?:\s*\+\s*(\d+)'?)?")


# ---------------------------------------------------------------------------
# Fetch (cached)
# ---------------------------------------------------------------------------

def _get_json(session: requests.Session, url: str, params: dict):
    delay = 2.0
    for _ in range(5):
        try:
            r = session.get(url, params=params, timeout=40)
        except requests.RequestException:
            time.sleep(delay); delay *= 2; continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code in (400, 404):
            return None
        time.sleep(delay); delay *= 2
    return None


def fetch_year(session, slug: str, year: int, refresh: bool) -> list[dict]:
    """All events of `slug` in calendar `year`, from the cache when present."""
    f = CACHE / slug / f'{year}.json.gz'
    if f.exists() and not refresh:
        with gzip.open(f, 'rt') as fh:
            return json.load(fh)
    url = BASE.format(slug=slug)
    d = _get_json(session, url, {'dates': str(year), 'limit': LIMIT})
    time.sleep(0.25)
    events = (d or {}).get('events') or []
    if len(events) >= LIMIT:
        # Capped: ask month by month instead.
        events = []
        for m in range(1, 13):
            dm = _get_json(session, url, {'dates': f'{year}{m:02d}', 'limit': LIMIT})
            events.extend((dm or {}).get('events') or [])
            time.sleep(0.25)
    if d is None and not events:
        return []          # unknown slug or no data: cache nothing, ask again next run
    f.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(f, 'wt') as fh:
        json.dump(events, fh)
    return events


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_clock(display: str | None) -> tuple[int | None, int]:
    if not display:
        return None, 0
    m = _CLOCK.search(display)
    if not m:
        return None, 0
    return int(m.group(1)), int(m.group(2) or 0)


def period_of(minute: int | None, shootout: bool) -> int | None:
    if shootout:
        return 5
    if minute is None:
        return None
    if minute <= 45:
        return 1
    if minute <= 90:
        return 2
    if minute <= 105:
        return 3
    return 4


def kind_of(d: dict) -> str:
    if d.get('shootout'):
        return 'shootout_goal' if d.get('scoringPlay') else 'shootout_miss'
    if d.get('scoringPlay'):
        if d.get('ownGoal'):
            return 'own_goal'
        if d.get('penaltyKick'):
            return 'penalty_goal'
        return 'goal'
    if d.get('redCard'):
        return 'red'
    if d.get('yellowCard'):
        return 'yellow'
    return 'other'


def parse_event(e: dict, slug: str) -> tuple[dict, list[dict]] | None:
    try:
        c = e['competitions'][0]
        sides = {x['homeAway']: x for x in c['competitors']}
        home, away = sides['home'], sides['away']
    except (KeyError, IndexError):
        return None
    st = c.get('status', {}).get('type', {}) or {}
    status = st.get('name')
    completed = bool(st.get('completed'))
    side_of = {str(home['team']['id']): 'H', str(away['team']['id']): 'A'}

    def score(x):
        try:
            return int(x.get('score'))
        except (TypeError, ValueError):
            return None

    hs, as_ = score(home), score(away)
    events = []
    for seq, d in enumerate(c.get('details') or []):
        minute, added = parse_clock((d.get('clock') or {}).get('displayValue'))
        shootout = bool(d.get('shootout'))
        ath = d.get('athletesInvolved') or []
        events.append({
            'seq': seq,
            'kind': kind_of(d),
            'type_text': (d.get('type') or {}).get('text'),
            'minute': minute,
            'added': added,
            'period': period_of(minute, shootout),
            'team_side': side_of.get(str((d.get('team') or {}).get('id'))),
            'player': ath[0].get('displayName') if ath else None,
        })

    goals = [x for x in events if x['kind'] in ('goal', 'own_goal', 'penalty_goal')]
    gh = sum(1 for x in goals if x['team_side'] == 'H')
    ga = sum(1 for x in goals if x['team_side'] == 'A')
    timeline_ok = (completed and hs is not None and as_ is not None
                   and gh == hs and ga == as_
                   and all(x['team_side'] and x['minute'] is not None for x in goals))
    ht_h = ht_a = None
    if timeline_ok:
        ht_h = sum(1 for x in goals if x['team_side'] == 'H' and x['period'] == 1)
        ht_a = sum(1 for x in goals if x['team_side'] == 'A' and x['period'] == 1)

    sname = (status or '').upper()
    went_pens = 'PEN' in sname or any(x['period'] == 5 for x in events)
    went_et = went_pens or 'AET' in sname or any((x['minute'] or 0) > 90 and x['period'] in (3, 4) for x in events)

    venue = (c.get('venue') or e.get('venue') or {}).get('fullName')
    att = c.get('attendance')
    season = e.get('season') or {}
    row = {
        'event_id': int(e['id']),
        'league_slug': slug,
        'league_code': SLUGS.get(slug),
        'season_year': season.get('year'),
        'season_slug': season.get('slug'),
        'kickoff_utc': e.get('date'),
        'status': status,
        'completed': completed,
        'home_espn_id': int(home['team']['id']) if str(home['team'].get('id', '')).isdigit() else None,
        'away_espn_id': int(away['team']['id']) if str(away['team'].get('id', '')).isdigit() else None,
        'home_name': home['team'].get('displayName') or home['team'].get('name') or '?',
        'away_name': away['team'].get('displayName') or away['team'].get('name') or '?',
        'home_score': hs,
        'away_score': as_,
        'home_score_ht': ht_h,
        'away_score_ht': ht_a,
        'went_to_et': went_et,
        'went_to_pens': went_pens,
        'venue': venue,
        'attendance': int(att) if isinstance(att, (int, float)) and att > 0 else None,
        'n_details': len(events),
        'timeline_ok': timeline_ok,
    }
    return row, events


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

MATCH_COLS = ['event_id', 'league_slug', 'league_code', 'season_year', 'season_slug',
              'kickoff_utc', 'status', 'completed', 'home_espn_id', 'away_espn_id',
              'home_name', 'away_name', 'home_score', 'away_score', 'home_score_ht',
              'away_score_ht', 'went_to_et', 'went_to_pens', 'venue', 'attendance',
              'n_details', 'timeline_ok']
EVENT_COLS = ['event_id', 'seq', 'kind', 'type_text', 'minute', 'added', 'period',
              'team_side', 'player']


def write(conn, rows: list[dict], events: list[dict]) -> None:
    cur = conn.cursor()
    upd = ', '.join(f'{c} = EXCLUDED.{c}' for c in MATCH_COLS if c != 'event_id')
    psycopg2.extras.execute_values(
        cur,
        f'INSERT INTO espn_matches ({", ".join(MATCH_COLS)}) VALUES %s '
        f'ON CONFLICT (event_id) DO UPDATE SET {upd}, fetched_at = now()',
        [tuple(r[c] for c in MATCH_COLS) for r in rows], page_size=500,
    )
    ids = [r['event_id'] for r in rows]
    cur.execute('DELETE FROM espn_match_events WHERE event_id = ANY(%s)', (ids,))
    if events:
        psycopg2.extras.execute_values(
            cur,
            f'INSERT INTO espn_match_events ({", ".join(EVENT_COLS)}) VALUES %s',
            [tuple(ev[c] for c in EVENT_COLS) for ev in events], page_size=2000,
        )
    conn.commit()


def crawl(slugs: list[str], years: list[int], refresh_current: bool, dry_run: bool) -> None:
    session = requests.Session()   # library default User-Agent, on purpose
    this_year = datetime.now(timezone.utc).year
    conn = None if dry_run else psycopg2.connect(ingest_url())
    totals = Counter()
    for slug in slugs:
        per_slug = Counter()
        for y in years:
            evs = fetch_year(session, slug, y, refresh=(refresh_current and y >= this_year - (1 if datetime.now(timezone.utc).month <= 1 else 0)))
            rows, events = [], []
            for e in evs:
                p = parse_event(e, slug)
                if p is None:
                    continue
                row, ev = p
                if not row['kickoff_utc']:
                    continue
                rows.append(row)
                for x in ev:
                    x['event_id'] = row['event_id']
                events.extend(ev)
            # A season straddling two calendar years appears in both requests;
            # keep one row per event id.
            seen = {}
            for r in rows:
                seen[r['event_id']] = r
            rows = list(seen.values())
            ev_keep = {(x['event_id'], x['seq']): x for x in events}
            events = list(ev_keep.values())
            per_slug['events'] += len(rows)
            per_slug['completed'] += sum(r['completed'] for r in rows)
            per_slug['timeline_ok'] += sum(bool(r['timeline_ok']) for r in rows)
            per_slug['details'] += len(events)
            if rows and not dry_run:
                write(conn, rows, events)
        totals.update(per_slug)
        if per_slug['events']:
            log.info(f'{slug:24s} events {per_slug["events"]:6d}  completed {per_slug["completed"]:6d}  '
                     f'timeline_ok {per_slug["timeline_ok"]:6d}  details {per_slug["details"]:7d}')
        else:
            log.info(f'{slug:24s} nothing (unknown slug or no coverage)')
    log.info(f'TOTAL {dict(totals)}')
    if conn:
        conn.close()


# ---------------------------------------------------------------------------
# Link to `matches`
# ---------------------------------------------------------------------------

LINK_WINDOW = timedelta(hours=36)
MIN_LINK = 0.6


def link(conn) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id, code FROM leagues")
    code_to_league = {c: i for i, c in cur.fetchall()}
    # Every name we know each team by: canonical + every alias.
    cur.execute("""SELECT t.id, t.canonical_name FROM teams t
                   UNION SELECT team_id, alias FROM team_aliases""")
    names = defaultdict(set)
    for tid, n in cur.fetchall():
        if n:
            names[tid].add(n)

    cur.execute("""SELECT DISTINCT league_code FROM espn_matches
                   WHERE league_code IS NOT NULL AND league_slug <> ALL(%s)""", (list(WOMENS),))
    codes = [r[0] for r in cur.fetchall() if r[0] in code_to_league]
    votes: dict[int, Counter] = defaultdict(Counter)     # ESPN team id -> our team ids
    pending = []
    for code in codes:
        cur.execute("""SELECT m.id, m.kickoff_utc, m.home_team_id, m.away_team_id,
                              m.home_score, m.away_score
                       FROM matches m JOIN seasons s ON s.id = m.season_id
                       WHERE s.league_id = %s AND m.home_score IS NOT NULL""",
                    (code_to_league[code],))
        ours = cur.fetchall()
        by_score = defaultdict(list)
        for mid, ko, h, a, hs, as_ in ours:
            by_score[(hs, as_)].append((mid, ko, h, a))
        cur.execute("""SELECT event_id, kickoff_utc, home_name, away_name, home_score, away_score,
                              home_espn_id, away_espn_id
                       FROM espn_matches
                       WHERE league_code = %s AND completed AND home_score IS NOT NULL""", (code,))
        theirs = cur.fetchall()
        proposals = []
        for eid, ko, hn, an, hs, as_, hid, aid in theirs:
            cands = []
            for mid, mko, h, a in by_score.get((hs, as_), ()):
                if abs(mko - ko) > LINK_WINDOW:
                    continue
                sh = max((team_score(hn, n) for n in names.get(h, ())), default=0.0)
                if sh < MIN_LINK:
                    continue
                sa = max((team_score(an, n) for n in names.get(a, ())), default=0.0)
                if sa < MIN_LINK:
                    continue
                cands.append(((sh + sa) / 2, mid, h, a))
            if not cands:
                continue
            cands.sort(reverse=True)
            if len(cands) > 1 and cands[1][0] >= cands[0][0] - 1e-9:
                continue     # a tie is not a match
            s, mid, h, a = cands[0]
            proposals.append((s, eid, mid, hid, aid, h, a))
        # One-to-one: the best-scoring claim on a match wins, the rest stay unlinked.
        proposals.sort(key=lambda p: -p[0])
        matched, used = {}, set()
        for s, eid, mid, hid, aid, h, a in proposals:
            if mid in used or eid in matched:
                continue
            used.add(mid)
            matched[eid] = (mid, round(s, 3))
            if hid is not None:
                votes[hid][h] += 1
            if aid is not None:
                votes[aid][a] += 1
        pending.append((code, by_score, theirs, matched, used, len(ours)))

    # ESPN's team ids pair what names could not: a match with ONE side proven
    # (an id with >= 2 agreeing name matches) whose candidates — same score,
    # inside the window, that team on that side — number exactly one is that
    # match, and the other id is learned from it. Repeat while it pairs.
    by_name = {p[0]: len(p[3]) for p in pending}
    while True:
        id_map = {i: next(iter(v)) for i, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}
        added = 0
        for code, by_score, theirs, matched, used, _ in pending:
            for eid, ko, hn, an, hs, as_, hid, aid in theirs:
                if eid in matched:
                    continue
                h, a = id_map.get(hid), id_map.get(aid)
                if h is None and a is None:
                    continue
                cands = [(mid, th, ta) for mid, mko, th, ta in by_score.get((hs, as_), ())
                         if mid not in used and abs(mko - ko) <= LINK_WINDOW
                         and (h is None or th == h) and (a is None or ta == a)]
                if len(cands) != 1:
                    continue
                mid, th, ta = cands[0]
                matched[eid] = (mid, 1.0)
                used.add(mid)
                if hid is not None:
                    votes[hid][th] += 1
                if aid is not None:
                    votes[aid][ta] += 1
                added += 1
        if not added:
            break

    total_linked = 0
    for code, by_score, theirs, matched, used, n_ours in pending:
        cur.execute('UPDATE espn_matches SET match_id = NULL, link_score = NULL WHERE league_code = %s', (code,))
        if matched:
            psycopg2.extras.execute_values(
                cur,
                """UPDATE espn_matches e SET match_id = v.mid, link_score = v.s
                   FROM (VALUES %s) AS v(eid, mid, s) WHERE e.event_id = v.eid""",
                [(e, m, s) for e, (m, s) in matched.items()], page_size=1000)
        conn.commit()
        total_linked += len(matched)
        log.info(f'link {code:12s} espn {len(theirs):6d}  ours {n_ours:6d}  linked {len(matched):6d} '
                 f'(by name {by_name[code]}, by team id {len(matched) - by_name[code]})')

    # ESPN's spelling becomes an alias where it only ever meant one of our teams.
    id_map = {i: next(iter(v)) for i, v in votes.items() if len(v) == 1 and sum(v.values()) >= 2}
    name_to_team: dict[str, set] = defaultdict(set)
    for code, by_score, theirs, matched, used, _ in pending:
        for eid, ko, hn, an, hs, as_, hid, aid in theirs:
            if hid in id_map:
                name_to_team[hn].add(id_map[hid])
            if aid in id_map:
                name_to_team[an].add(id_map[aid])
    added = 0
    for name, tids in name_to_team.items():
        if len(tids) == 1:
            cur.execute("""INSERT INTO team_aliases (team_id, source, alias)
                           VALUES (%s, 'espn', %s) ON CONFLICT (source, alias) DO NOTHING""",
                        (next(iter(tids)), name))
            added += cur.rowcount
    conn.commit()
    log.info(f'linked {total_linked} ESPN matches; {len(id_map)} ESPN teams mapped; {added} new espn team aliases')


def report(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT league_slug, count(*), count(*) FILTER (WHERE completed),
               count(*) FILTER (WHERE timeline_ok), count(match_id),
               min(kickoff_utc)::date, max(kickoff_utc)::date,
               (SELECT count(*) FROM espn_match_events x WHERE x.event_id = ANY(array_agg(e.event_id)) AND x.kind = 'red')
        FROM espn_matches e GROUP BY 1 ORDER BY 2 DESC""")
    print(f'{"slug":24s} {"events":>7s} {"done":>7s} {"tl_ok":>7s} {"linked":>7s}  {"from":10s} {"to":10s} {"reds":>6s}')
    for r in cur.fetchall():
        print(f'{r[0]:24s} {r[1]:7d} {r[2]:7d} {r[3]:7d} {r[4]:7d}  {r[5]} {r[6]} {r[7]:6d}')
    cur.execute("SELECT count(*), count(*) FILTER (WHERE timeline_ok), count(match_id) FROM espn_matches")
    print('TOTAL events / timeline_ok / linked:', cur.fetchone())
    cur.execute("SELECT kind, count(*) FROM espn_match_events GROUP BY 1 ORDER BY 2 DESC")
    print('event kinds:', cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--slugs', nargs='+', default=list(SLUGS))
    ap.add_argument('--years', nargs='+', type=int)
    ap.add_argument('--refresh-current', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--link-only', action='store_true')
    ap.add_argument('--no-link', action='store_true')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    years = args.years or list(range(FIRST_YEAR, datetime.now(timezone.utc).year + 1))
    if args.report:
        conn = psycopg2.connect(ingest_url()); report(conn); conn.close(); return
    if not args.link_only:
        crawl(args.slugs, years, args.refresh_current, args.dry_run)
    if not args.dry_run and not args.no_link:
        conn = psycopg2.connect(ingest_url())
        link(conn)
        conn.close()


if __name__ == '__main__':
    main()
