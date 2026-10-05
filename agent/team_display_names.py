"""
Display names for the site's clubs — ESPN's spelling, keyed by our teams.id.

`teams.canonical_name` is Football-Data's spelling, and Football-Data
abbreviates: "Sp Lisbon", "Ath Madrid", "Nott'm Forest", "M'gladbach",
"Sociedad", "Peterboro". Those are the keys the models join on and must not
change, but nobody searches for them and a page titled with them is found by
nobody. This writes site/app/lib/team_names.json, which the site reads through
lib/teamDisplay.ts for every name it SHOWS; matching keeps using the canonical.

Every club listed on /teams (a domestic league match in the last 120 days) is
matched against ESPN's team list for the same league, one league at a time. A
league holds ~20 clubs, so a match needs only to beat 19 neighbours, and each
ESPN team is used at most once. Anything under the score bar is left out and
keeps its canonical name: a stale spelling is better than the wrong club.

    python team_display_names.py                     # rebuild the JSON (needs DATABASE_URL)
    python team_display_names.py --dry-run           # print every mapping, write nothing
    python team_display_names.py --teams ours.json   # [[id, canonical, league_code], ...] instead of the DB

⚠️ ESPN's league lists follow ITS promotion calendar, ours follow the last
result Football-Data carried. Early in a season a promoted club can sit in a
different ESPN league from the one we file it under; it then goes unmatched and
is reported, never forced onto a neighbour.

⚠️ Do not set a User-Agent: ESPN's edge refuses browser-shaped and custom ones
(see agent/espn_stats.py).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "app" / "lib" / "team_names.json"

# Our league code → ESPN's.
LEAGUES = {
    "BEL-JPL": "bel.1", "ENG-PR": "eng.1", "ENG-CH": "eng.2", "ENG-L1": "eng.3", "ENG-L2": "eng.4",
    "ENG-CON": "eng.5", "ESP-LL": "esp.1", "ESP-L2": "esp.2", "FRA-L1": "fra.1", "FRA-L2": "fra.2",
    "GER-BL1": "ger.1", "GER-BL2": "ger.2", "GRE-SL": "gre.1", "ITA-SA": "ita.1", "ITA-SB": "ita.2",
    "NED-ED": "ned.1", "POR-PL": "por.1", "SCO-PR": "sco.1", "SCO-CH": "sco.2", "SCO-L1": "sco.3",
    "SCO-L2": "sco.4", "TUR-SL": "tur.1",
}

# Hand decisions, by our id. They win over ESPN, and they are the ONLY names
# that do not come from the matcher, so each one says why.
OVERRIDES = {
    279: "Athletic Club",           # ESPN's own name; "Ath Bilbao" shares no word with it
    286: "Deportivo La Coruña",     # ESPN prints just "Deportivo", which is three clubs in Spain
    611: "Airdrieonians",           # ESPN files it a division away early in the season
    488: "Vitória de Guimarães",    # ESPN drops the tilde
    222: "Inter Milan",             # ESPN's "Internazionale" is not what an English reader types
    1165: "Celta Fortuna",          # Celta's reserve side, renamed; ESPN's "RC Celta Fortuna" scores under the bar
}

FOLD = str.maketrans({"ø": "o", "Ø": "o", "ß": "ss", "æ": "ae", "đ": "d", "ł": "l", "ı": "i", "İ": "i"})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.translate(FOLD))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Football-Data's abbreviations, spelled out. Only used to SCORE a candidate,
# never shown.
EXPAND = {
    "man": "manchester", "nott m": "nottingham", "ath": "athletic atletico", "sp": "sporting",
    "ein": "eintracht", "m gladbach": "monchengladbach", "weds": "wednesday", "rvs": "rovers",
    "sth": "south", "utd": "united", "peterboro": "peterborough", "for": "fortuna",
    "buyuksehyr": "basaksehir istanbul", "goztep": "goztepe", "inverness c": "inverness caledonian thistle",
    "st": "saint st", "gilloise": "union gilloise", "olympiakos": "olympiacos", "levadeiakos": "levadiakos",
    "panetolikos": "panaitolikos", "koln": "cologne koln", "nurnberg": "nurnberg nuremberg",
    "bayern munich": "bayern munich munchen", "espanol": "espanyol", "coruna": "coruna deportivo",
    "santander": "racing santander", "sociedad": "real sociedad", "betis": "real betis",
    "celta": "celta vigo", "den haag": "den haag ado", "nijmegen": "nec nijmegen", "zwolle": "pec zwolle",
    "guimaraes": "vitoria guimaraes", "estrela": "estrela amadora", "standard": "standard liege",
    "waregem": "zulte waregem", "antwerp": "royal antwerp", "st truiden": "sint truiden truidense",
    "charleroi": "sporting charleroi", "genk": "racing genk krc", "oud heverlee leuven": "oh leuven oud heverlee",
    "hertha": "hertha berlin bsc", "dresden": "dynamo dresden", "greuther furth": "spvgg greuther furth",
    "paris sg": "paris saint germain", "st etienne": "saint etienne", "queens park": "queen s park queens park",
    "wolves": "wolverhampton wanderers", "tottenham": "tottenham hotspur", "amedspor": "amed",
    "erzurumspor": "erzurum", "rizespor": "caykur rizespor",
}
NOISE = set((
    "fc cf sc afc ac cd club de the sv fk sk kv krc rsc sco sad vfl vfb tsg ss ssc us as calcio 1 04 bsc "
    "and hove albion town city united county athletic"
).split())
MIN_SCORE = 0.5


def _keys(name: str) -> set[str]:
    n = norm(name)
    extra = [v for k, v in EXPAND.items() if re.search(r"(^| )" + re.escape(k) + r"($| )", n)]
    return set(n.split()) | set(" ".join(extra).split())


def score(ours: str, espn: str) -> float:
    a = _keys(ours) - NOISE
    b = set(norm(espn).split()) - NOISE
    if not a or not b:
        return 0.0
    hit = float(len(a & b))
    # An abbreviation is a prefix: "peterboro" / "peterborough".
    for x in a - b:
        if len(x) >= 3 and any(y.startswith(x) or x.startswith(y) for y in b if len(y) >= 3):
            hit += 0.8
    return hit / min(len(a), len(b))


def espn_teams(code: str) -> list[dict]:
    r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams", timeout=30)
    r.raise_for_status()
    return [t["team"] for t in r.json()["sports"][0]["leagues"][0]["teams"]]


# A one-word short name ("Atlético") shares its only word with every club that
# has it, so it scores 1.0 for Athletic Bilbao as readily as for Atlético
# Madrid. It still counts — "Gladbach" is how M'gladbach is found — but a
# full display name that fits as well always outranks it.
SHORT_WEIGHT = 0.9


def match_league(ours: list[tuple[int, str]], teams: list[dict]) -> tuple[dict, list, list]:
    """Best-first assignment inside one league: each club and each ESPN team
    used once. Two clubs tied for the same ESPN team is not decided by list
    order — both are left out (fails closed, the rule every join in this repo
    follows). Returns ({id: team}, unmatched ids, contested ids)."""
    cands = []
    for i, name in ours:
        for t in teams:
            full = t.get("displayName") or ""
            short = t.get("shortDisplayName") or ""
            loc = t.get("location") or ""
            s = max(
                score(name, full) if full else 0.0,
                score(name, loc) if loc else 0.0,
                SHORT_WEIGHT * score(name, short) if short else 0.0,
            )
            if norm(name) in (norm(full), norm(short)):
                s = 9.0
            cands.append((round(s, 6), i, t))
    cands.sort(key=lambda c: -c[0])
    got, used, contested = {}, set(), set()
    for s, i, t in cands:
        if s < MIN_SCORE or i in got or i in contested or t["displayName"] in used:
            continue
        rivals = [
            j for s2, j, t2 in cands
            if j != i and s2 == s and t2["displayName"] == t["displayName"] and j not in got
        ]
        if rivals:
            contested.update([i, *rivals])
            used.add(t["displayName"])
            continue
        got[i] = t
        used.add(t["displayName"])
    unmatched = [i for i, _ in ours if i not in got and i not in contested]
    return got, unmatched, sorted(contested)


def load_ours(path: str | None) -> list[tuple[int, str, str]]:
    if path:
        return [tuple(x) for x in json.load(open(path))]
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(ROOT / "ingest" / ".env")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    with conn.cursor() as cur:
        # The same universe as listTeams() in site/app/lib/teampage.ts.
        cur.execute(
            """
            with recent as (
              select m.home_team_id tid, s.league_id, m.kickoff_utc from matches m join seasons s on s.id = m.season_id
               where m.kickoff_utc > now() - interval '120 days' and m.home_score is not null
              union all
              select m.away_team_id, s.league_id, m.kickoff_utc from matches m join seasons s on s.id = m.season_id
               where m.kickoff_utc > now() - interval '120 days' and m.home_score is not null
            ), latest as (
              select distinct on (r.tid) r.tid, r.league_id
                from recent r join leagues l on l.id = r.league_id
               where not l.is_cup and coalesce(l.country, '') <> 'INTL' and l.name <> 'NBA'
                 and r.tid in (select tid from recent group by tid having count(*) >= 3)
               order by r.tid, r.kickoff_utc desc
            )
            select t.id, t.canonical_name, l.code
              from latest x join teams t on t.id = x.tid join leagues l on l.id = x.league_id
            """
        )
        rows = cur.fetchall()
    conn.close()
    return [(int(i), n, c) for i, n, c in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--teams", help="JSON [[id, canonical, league_code], ...] instead of the database")
    args = ap.parse_args()

    ours = load_ours(args.teams)
    by = collections.defaultdict(list)
    for i, name, code in ours:
        by[code].append((i, name))

    # Keep what is already in the file for clubs this run does not see, so a
    # club that drops out of the 120-day window keeps its name on its page.
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    unmatched = []
    for code, clubs in sorted(by.items()):
        espn = LEAGUES.get(code)
        if not espn:
            unmatched += [(code, i, n, "no ESPN league") for i, n in clubs]
            continue
        got, missing, contested = match_league(clubs, espn_teams(espn))
        names = dict(clubs)
        for i in contested:
            if i in OVERRIDES:
                missing.append(i)
            else:
                unmatched.append((code, i, names[i], "tied with another club for one ESPN team"))
        for i, t in got.items():
            out[str(i)] = {
                "name": OVERRIDES.get(i, t["displayName"]),
                "short": (t.get("shortDisplayName") or "").strip() or None,
                "abbr": t.get("abbreviation") or None,
            }
            if norm(names[i]) != norm(out[str(i)]["name"]):
                print(f"{code:8} {i:5}  {names[i]:24} -> {out[str(i)]['name']}")
        for i in missing:
            if i in OVERRIDES:
                out[str(i)] = {"name": OVERRIDES[i]}
                print(f"{code:8} {i:5}  {names[i]:24} -> {OVERRIDES[i]}  (override)")
            else:
                unmatched.append((code, i, names[i], "no ESPN team cleared the bar"))

    out = {k: {kk: vv for kk, vv in v.items() if vv} for k, v in sorted(out.items(), key=lambda kv: int(kv[0]))}
    print(f"\n{len(out)} clubs named; {len(unmatched)} keep their canonical name:")
    for u in unmatched:
        print("  ", *u)
    if not args.dry_run:
        OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
