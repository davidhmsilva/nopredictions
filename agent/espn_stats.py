"""Live match statistics from ESPN's public soccer API — no key, no quota.

Why this exists: on 2026-09-06 api-football's daily allowance ran out with only
7,176 calls of our own recorded against a supposed 75,000, and it had been
serving 0.0% xG since 09-02. The pressure arms went blind. This is a second
source that costs nothing and cannot be exhausted.

🔑 **One call per LEAGUE returns every live match's statistics.** api-football
needs one call for the live list plus one per fixture for its stats — 2,188 of
our 3,691 pressure calls that day were the per-fixture half. Here the whole
league arrives together, so a busy Saturday costs the same as a quiet Tuesday:
one request per league per cycle, full stop.

    https://site.api.espn.com/apis/site/v2/sports/soccer/<code>/scoreboard

⚠️ This is not a product ESPN sells — it is the backend of their website. There
is no documentation, no SLA and no changelog, and it can change or close without
notice. FotMob's equivalent did exactly that: the endpoint everyone used now
returns 404 without a signed header. So this is built as a SECOND source that
degrades to api-football, never as the only one.

What it carries per team, live: shots on target, corners, possession, total
shots, fouls. What it does NOT carry at team level: xG, and shots inside the
box. Both are handled by `danger_index`'s renormalisation rather than by passing
zeros — a zero says "nobody got into the box", and the truth is "nobody told
us". On a real reading the difference is small: the same match scores 58.50 on a
full feed, 57.50 without xG and 56.67 in ESPN's shape.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from fixture_match import pair_score

log = logging.getLogger("espn")

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Verified reachable 2026-09-06 against the leagues where strategy 16 had a
# Polymarket book in the preceding three days: 18 of 19 answered. K League 1 is
# the one that did not resolve under `kor.1` and is left out rather than
# guessed at — a wrong code returns someone else's fixtures, silently.
LEAGUES: dict[str, str] = {
    "usa.1": "Major League Soccer",
    "eng.1": "Premier League",
    "eng.2": "Championship",
    "eng.3": "League One",
    "eng.4": "League Two",
    "esp.1": "LaLiga",
    "esp.2": "Segunda División",
    "ita.1": "Serie A",
    "ita.2": "Serie B",
    "ger.1": "Bundesliga",
    "ger.2": "2. Bundesliga",
    "fra.1": "Ligue 1",
    "fra.2": "Ligue 2",
    "ned.1": "Eredivisie",
    "ned.2": "Eerste Divisie",
    "por.1": "Primeira Liga",
    "bel.1": "Pro League",
    "tur.1": "Süper Lig",
    "sui.1": "Super League",
    "aut.1": "Bundesliga (AUT)",
    "sco.1": "Premiership",
    "nor.1": "Eliteserien",
    "swe.1": "Allsvenskan",
    "den.1": "Superliga",
    "mex.1": "Liga MX",
    "bra.1": "Brasileirão",
    "arg.1": "Liga Profesional Argentina",
    "jpn.1": "J1 League",
    # Leagues api-football lists live but publishes NO statistics for — see
    # AF_UNCOVERED_TO_ESPN below. Checked on the 2026-09-12 scoreboards: every
    # event carried shots, except one of two in Costa Rica.
    "eng.5": "National League",
    "usa.usl.1": "USL Championship",
    "usa.usl.l1": "USL League One",
    "arg.2": "Primera Nacional",
    "ven.1": "Primera División (VEN)",
    "crc.1": "Primera División (CRC)",
}

# api-football league id -> ESPN code, for competitions api-football CONFIRMS it
# has no statistics for (`coverage.fixtures.statistics_fixtures = false`, all six
# checked 2026-09-13). On 2026-09-12 these held 33 of the 49 Polymarket-listed
# fixtures the first-half arms skipped for want of any reading.
#
# Keyed on the league ID, never the name: "Primera División" was three countries
# in one day's feed. Uruguay's is uncovered too, but ESPN carries no stats for it
# either, so it is left out rather than mapped to an empty board. A wrong code
# returns someone else's fixtures silently; team matching (a tie fails closed)
# and the score check in live_tracker are the guard behind this table.
AF_UNCOVERED_TO_ESPN: dict[int, str] = {
    43: "eng.5",        # National League (England)
    255: "usa.usl.1",   # USL Championship
    489: "usa.usl.l1",  # USL League One
    129: "arg.2",       # Primera Nacional (Argentina)
    299: "ven.1",       # Primera División (Venezuela)
    162: "crc.1",       # Primera División (Costa Rica)
}

_TIMEOUT = 12
# ESPN answered ten back-to-back requests in ~55ms each with no throttling and
# no rate-limit header. That is not a licence to hammer it: one pass per cycle
# over the leagues that have something live is the intended shape.
#
# ⚠️ DO NOT SET A USER-AGENT HERE. Measured 2026-09-06:
#
#     curl/8.x            -> 200      Mozilla/5.0 (browser)          -> 403
#     python-requests/…   -> 200      nopredictions/1.0 (+url…)      -> 403
#
# The edge rejects browser-shaped and custom agents and serves plain library
# defaults, so a "polite" self-identifying header is the one thing that breaks
# it — the first version of this file set one and every league 403'd. The
# library default is also the honest string: it says a Python script is calling,
# which is true. Sending a browser UA would get through too and is NOT done,
# because that is claiming to be something we are not.


@dataclass
class EspnTeamStats:
    """One side of one fixture, as ESPN reports it right now."""
    shots_on: int = 0
    shots_total: int = 0
    corners: int = 0
    possession: float = 50.0
    fouls: int = 0
    goals: int = 0


@dataclass
class EspnFixture:
    event_id: str
    league_code: str
    league: str
    home: str
    away: str
    minute: int | None
    state: str                      # 'pre' | 'in' | 'post'
    detail: str
    home_stats: EspnTeamStats = field(default_factory=EspnTeamStats)
    away_stats: EspnTeamStats = field(default_factory=EspnTeamStats)

    @property
    def live(self) -> bool:
        return self.state == "in"

    @property
    def has_stats(self) -> bool:
        """A scoreboard block with every counter at zero is what a match that has
        not started looks like, and also what a genuinely goalless first two
        minutes looks like. Possession is the discriminator: ESPN does not
        publish it at all until play begins."""
        return bool(self.home_stats.shots_total or self.away_stats.shots_total
                    or self.home_stats.possession != 50.0)


def _num(v, cast=int, default=0):
    try:
        return cast(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return default


def _stats_of(competitor: dict) -> EspnTeamStats:
    raw = {s.get("name"): s.get("displayValue") for s in competitor.get("statistics") or []}
    return EspnTeamStats(
        shots_on=_num(raw.get("shotsOnTarget")),
        shots_total=_num(raw.get("totalShots")),
        corners=_num(raw.get("wonCorners")),
        possession=_num(raw.get("possessionPct"), float, 50.0),
        fouls=_num(raw.get("foulsCommitted")),
        goals=_num(competitor.get("score")),
    )


def _minute_of(status: dict) -> int | None:
    """ESPN's clock, from `displayClock` ("67'", "90'+5'") with `clock` as the
    fallback. Stoppage time is FOLDED IN — 90'+5' reads as 95 — because every
    table on this project is keyed on a minute and 90 for both the 90th and the
    95th minute is a lie the tables cannot see through."""
    detail = str(status.get("displayClock") or "").strip()
    if detail:
        parts = [p for p in detail.replace("'", " ").replace("+", " ").split() if p.isdigit()]
        if parts:
            return sum(int(p) for p in parts)
    secs = status.get("clock")
    return int(secs // 60) if isinstance(secs, (int, float)) and secs else None


def fetch_league(code: str, session: requests.Session | None = None) -> list[EspnFixture]:
    """Every fixture on one league's scoreboard, with live stats. One request."""
    get = (session or requests).get
    try:
        res = get(f"{BASE}/{code}/scoreboard", timeout=_TIMEOUT)
        res.raise_for_status()
        data = res.json()
    except Exception as exc:                       # noqa: BLE001 — a dead league is not fatal
        log.warning("[espn] %s unreachable: %s", code, exc)
        return []

    out: list[EspnFixture] = []
    for ev in data.get("events") or []:
        comps = (ev.get("competitions") or [{}])[0]
        sides = comps.get("competitors") or []
        if len(sides) != 2:
            continue
        # ESPN orders competitors [home, away] but publishes homeAway on each;
        # reading the flag rather than the order is the difference between a
        # possession figure on the right team and one that inverts the reading.
        home = next((s for s in sides if s.get("homeAway") == "home"), sides[0])
        away = next((s for s in sides if s.get("homeAway") == "away"), sides[1])
        status = (comps.get("status") or {}).get("type") or {}

        out.append(EspnFixture(
            event_id=str(ev.get("id") or ""),
            league_code=code,
            league=LEAGUES.get(code, code),
            home=str((home.get("team") or {}).get("displayName") or ""),
            away=str((away.get("team") or {}).get("displayName") or ""),
            minute=_minute_of(comps.get("status") or {}),
            state=str(status.get("state") or "unknown"),
            detail=str(status.get("detail") or ""),
            home_stats=_stats_of(home),
            away_stats=_stats_of(away),
        ))
    return out


def live_fixtures(codes: list[str] | None = None) -> list[EspnFixture]:
    """Every in-play fixture ESPN knows about, across the mapped leagues."""
    session = requests.Session()
    found: list[EspnFixture] = []
    for code in (codes or list(LEAGUES)):
        found.extend(f for f in fetch_league(code, session) if f.live)
    return found


def match_fixture(pm_home: str, pm_away: str, fixtures: list[EspnFixture],
                  league: str | None = None) -> EspnFixture | None:
    """The ESPN fixture for a Polymarket pair, or None.

    Uses the project's alias-aware scorer, never substring containment — the
    rule that exists because "Real Madrid" and "Real Sociedad" share a word and
    a first-word match once paired River Plate with Platense. A TIE returns
    None: two fixtures scoring equally means the names cannot separate them, and
    picking either is the bug rather than the fix.
    """
    scored = [(pair_score(pm_home, pm_away, f.home, f.away, league), f) for f in fixtures]
    scored = [(s, f) for s, f in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None
    return scored[0][1]


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", help="one ESPN code, e.g. esp.1")
    ap.add_argument("--all", action="store_true", help="every mapped league")
    args = ap.parse_args()

    codes = [args.league] if args.league else list(LEAGUES)
    t0 = time.time()
    session = requests.Session()
    rows = [f for c in codes for f in fetch_league(c, session)]
    live = [f for f in rows if f.live]

    print(f"{len(codes)} leagues · {len(rows)} fixtures · {len(live)} live "
          f"· {time.time() - t0:.1f}s · {len(codes)} requests")
    print()
    for f in live or rows[:12]:
        h, a = f.home_stats, f.away_stats
        flag = "" if f.has_stats else "  (no stats yet)"
        print(f"  {f.league:<26} {f.home[:20]:>20} {h.goals}-{a.goals} {f.away[:20]:<20} "
              f"{str(f.minute) + chr(39) if f.minute else f.detail[:7]:>6}")
        print(f"  {'':<26} {'SOT ' + str(h.shots_on):>20}   {'SOT ' + str(a.shots_on):<20}"
              f"  corners {h.corners}-{a.corners}  poss {h.possession:.0f}-{a.possession:.0f}{flag}")


if __name__ == "__main__":
    _main()
