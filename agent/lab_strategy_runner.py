"""lab_strategy_runner.py — paper-trade the agents users deployed from Create.

A user describes an idea on Create, sees its strategy and backtest, and deploys
it (`strategies` with source='lab', db/049) — or copies someone else's from the
Marketplace, which is the same row under a new owner. This is what "deployed"
means: every cycle, each running spec is held against today's Polymarket
football boards, and a fixture that fits gets one paper trade — 1u at the real
CLOB ask, logged before kickoff, settled on the token that was bought.

    cd agent && source ../ingest/.venv/bin/activate
    python lab_strategy_runner.py --once --dry-run    # one cycle, no writes
    python lab_strategy_runner.py --once              # one cycle
    python lab_strategy_runner.py --settle            # settle finished trades

Driven by cron through cron_guard.sh — a short job that exits, so it can never
stack the way a forever-loop can.

WHAT IT WILL NOT DO, each a way the live trade would silently stop being the
rule the user tested:

  * Trade a competition the backtest never saw. "All leagues" in the Lab means
    the 22 leagues in bt_features, not every board Polymarket lists, and for both
    teams to score and the other goal lines, the leagues Polymarket's own history
    covers (db/085).
  * Enter early. The backtest bought at Pinnacle's CLOSE, so entries wait for
    the last ENTRY_WINDOW_MIN minutes before kickoff.
  * Trust Polymarket's title order for home and away. NBA titles are
    "Away vs. Home" and a side error inverts the bet rather than blunting it,
    so any rule that depends on sides (home/away, favourite, a named team)
    needs ESPN to confirm the orientation or the fixture is skipped.
  * Buy into a broken book. The 100-game review found the spread, not the
    depth, is what separates a price from a parked order.
  * Settle by "first outcome won". The resolver does that, which is a LOSS
    recorded as a win on an Under. Settled here by the token actually bought.
  * Guess a team's form. Everything a spec can ask about a team's recent games
    (rest, the last 5, its last game, its runs) is read live from ESPN, by the
    backtest's own definitions (`form_of`). A team whose history cannot be read,
    or is too short to answer, fails the rule — exactly as a NULL fails it in
    the backtest.
  * Evaluate anything else it cannot compute. Such a spec is paused with the
    reason written to `run_blocker`, where the site shows it.

Paper only. Nothing in this file can place an order.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ingest/.env"))

import db_txn                                                      # noqa: E402
import espn_stats                                                  # noqa: E402
from fixture_match import MIN_SIDE_SCORE, team_score               # noqa: E402
from late_goals_observer import _fetch_book                        # noqa: E402

log = logging.getLogger("lab_runner")

DATABASE_URL = os.getenv("DATABASE_URL")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# The season being traded, as a Lab season START year (2026-27 → 2026).
CURRENT_SEASON = 2026
ENTRY_WINDOW_MIN = 45          # enter only this close to kickoff
ESPN_BY_DATE = False           # --window sets it
MAX_SPREAD = 0.05              # ask − bid; wider and the quote is not a price
MIN_ASK_DEPTH_USD = 50.0       # notional on the top five ask levels
MIN_ASK, MAX_ASK = 0.02, 0.98
STAKE_UNITS = 1.0
GAMMA_PAGES = 20               # 100 events a page (Gamma caps `limit` at 100)

# What a spec can ask about a team's recent games, each under home_ and away_.
# Read live since 2026-10-30 (they used to pause the agent: the most natural
# ideas — "in poor form", "on short rest" — could be tested and never deployed).
TEAM_FIGURES = (
    "rest_days", "form_pts5", "avg_tg5", "avg_gf5", "avg_ga5", "venue_pts5",
    "last_gd", "win_run", "unbeaten_run", "loss_run", "winless_run",
)
FORM_FILTERS = tuple(f"{side}_{name}" for side in ("home", "away") for name in TEAM_FIGURES)
RUN_CAP = 10                   # a run is counted this far back, on both sides (db/083)

# Lab league code → the Polymarket competition tags that mean it, by slug and by
# label (lowercased). Built from the tags actually stored in pm_markets and a
# live board, 2026-09-11. Anything unlisted is NOT traded rather than guessed:
# "Premier League (Kazakhstan)" and "Brazil Serie A" are real labels, which is
# exactly why nothing here matches on a substring.
PM_COMPETITIONS: dict[str, tuple[set[str], set[str]]] = {
    "ENG-PR":  ({"epl", "premier-league"}, {"epl", "premier league", "english premier league"}),
    "ENG-CH":  ({"efl-championship", "championship"}, {"efl championship", "championship"}),
    "ENG-L1":  ({"efl-league-one", "league-one"}, {"efl league one", "league one"}),
    "ENG-L2":  ({"efl-league-two", "league-two"}, {"efl league two", "league two"}),
    "ENG-CON": ({"national-league"}, {"national league"}),
    "ESP-LL":  ({"laliga", "la-liga"}, {"la liga", "laliga"}),
    "ESP-L2":  ({"laliga-2", "la-liga-2", "segunda-division"}, {"la liga 2", "laliga 2", "segunda división", "segunda division"}),
    "ITA-SA":  ({"sea", "serie-a"}, {"serie a"}),
    "ITA-SB":  ({"seb", "serie-b"}, {"serie b"}),
    "GER-BL1": ({"bundesliga"}, {"bundesliga"}),
    "GER-BL2": ({"bundesliga-2", "2-bundesliga"}, {"bundesliga 2", "2. bundesliga"}),
    "FRA-L1":  ({"ligue-1"}, {"ligue 1"}),
    "FRA-L2":  ({"ligue-2"}, {"ligue 2"}),
    "NED-ED":  ({"eredivisie"}, {"eredivisie"}),
    "POR-PL":  ({"primeira-liga", "liga-portugal"}, {"primeira liga", "liga portugal"}),
    "BEL-JPL": ({"belgium-pro-league", "jupiler-pro-league"}, {"belgium pro league", "jupiler pro league"}),
    "TUR-SL":  ({"super-lig", "turkey-super-lig"}, {"süper lig", "super lig"}),
    "GRE-SL":  ({"greece-super-league"}, {"greece super league", "super league greece"}),
    "SCO-PR":  ({"scottish-premiership"}, {"scottish premiership"}),
    "SCO-CH":  ({"scottish-championship"}, {"scottish championship"}),
    "SCO-L1":  ({"scottish-league-one"}, {"scottish league one"}),
    "SCO-L2":  ({"scottish-league-two"}, {"scottish league two"}),
}
LAB_CODES = frozenset(PM_COMPETITIONS)

# Where ESPN can confirm home/away. A league missing here can still run a rule
# that does not care about sides (draw, over, under), and nothing else.
#
# ⚠️ Scottish League One and Two are out on purpose. ESPN answers sco.3 and sco.4
#    happily, with the 2025-26 season frozen on 2025-09-27 (checked 2026-10-31): a
#    fixture it still lists could confirm the wrong way round, and a team's form
#    would be a year old. A code answering is not a code being current.
ESPN_CODE = {
    "ENG-PR": "eng.1", "ENG-CH": "eng.2", "ENG-L1": "eng.3", "ENG-L2": "eng.4",
    "ENG-CON": "eng.5",
    "ESP-LL": "esp.1", "ESP-L2": "esp.2", "ITA-SA": "ita.1", "ITA-SB": "ita.2",
    "GER-BL1": "ger.1", "GER-BL2": "ger.2", "FRA-L1": "fra.1", "FRA-L2": "fra.2",
    "NED-ED": "ned.1", "POR-PL": "por.1", "BEL-JPL": "bel.1", "TUR-SL": "tur.1",
    "GRE-SL": "gre.1",
    "SCO-PR": "sco.1", "SCO-CH": "sco.2",
}

_TITLE_RE = re.compile(r"^(.+?)\s+vs\.?\s+(.+?)$", re.I)
_WIN_RE = re.compile(r"^will\s+(?P<team>.+?)\s+win(?:\s+on\s+[\d-]+)?\??$", re.I)
_DRAW_RE = re.compile(r"\bdraw\b", re.I)

# Over/under goals, by the `line` Gamma gives the market. Each value is a Lab market.
# ⚠️ Replayed in Postgres: ou25 at Pinnacle's close, the rest and btts at Polymarket's
#    own prices (db/085). VENUE_MARKETS in site/app/lib/backtest.ts is that list.
GOAL_LINES = {1.5: "ou15", 2.5: "ou25", 3.5: "ou35", 4.5: "ou45"}
LINE_OF = {market: line for line, market in GOAL_LINES.items()}
BTTS = "btts"
GOAL_MARKETS = frozenset(GOAL_LINES.values()) | {BTTS}
# Never on Polymarket's board in the history behind db/085, so these markets have no
# replay there. O/U 2.5 has Pinnacle's close instead, and keeps every league.
NO_VENUE_HISTORY = frozenset({"SCO-CH", "SCO-L1", "SCO-L2"})


# ── the spec ─────────────────────────────────────────────────────────────────

def run_blocker(spec: dict) -> str | None:
    """Why this spec cannot run live, or None. Same wording as the site
    (runBlocker in site/app/lib/agents.ts); this file is the authority: it
    re-checks every running spec and writes run_blocker itself."""
    if spec.get("market") != "1x2" and spec.get("market") not in GOAL_MARKETS:
        return ("NBA ideas are backtest-only: the NBA data ends in 2021-22 and "
                "there are no NBA boards this agent trades.")
    end = spec.get("season_end")
    if end is not None and end < CURRENT_SEASON - 1:
        return (f"Tested only up to the {end}-{str(end + 1)[2:]} season; running it "
                "today would trade outside what was tested.")
    if spec.get("side") in RELATIVE_SIDES and not spec.get("team") and not (
            spec.get("side") == "team" and spec.get("fav_status")):
        return ("This rule does not say which team it backs: it has to name one, "
                "or say the favourite or the underdog.")
    unread = unreadable_bounds(spec)
    if unread:
        return (f"This rule has a condition this agent cannot read yet ({', '.join(unread)}), "
                "and running it without that condition would trade a different rule.")
    return None


TEAM_LISTS = ("home_team_in", "home_team_not_in", "away_team_in", "away_team_not_in")
RELATIVE_SIDES = ("team", "opponent")


def flat_legs(spec: dict) -> list[dict]:
    """A rule about "the team" (a named club home or away, the favourite at
    whichever end it is) as the two plain rules it stands for: that team at home,
    and that team away. team_* and opp_* bounds become home_* and away_* on each.

    Everything below this line, like the backtest, reads only plain rules, so
    nothing downstream can disagree about who "the team" is. Mirrors flatLegs in
    site/app/lib/backtest.ts."""
    side = spec.get("side")
    if side not in RELATIVE_SIDES and not spec.get("team"):
        return [spec]
    legs = []
    for own, other in (("home", "away"), ("away", "home")):
        leg = {k: v for k, v in spec.items() if k != "team" and not k.startswith(("team_", "opp_"))}
        if side == "team":
            leg["side"] = own
        elif side == "opponent":
            leg["side"] = other
        if spec.get("team"):
            leg[f"{own}_team"] = spec["team"]
        for k, v in spec.items():
            if k.startswith("team_"):
                leg[f"{own}_{k[5:]}"] = v
            elif k.startswith("opp_"):
                leg[f"{other}_{k[4:]}"] = v
        legs.append(leg)
    return legs


def unreadable_bounds(spec: dict) -> list[str]:
    """Bounds this runner does not know, which it would otherwise walk straight
    past and trade a broader rule than the one tested. Happens when the site
    learns a condition before this file does."""
    known = {"odds", *FORM_FILTERS}
    return sorted({k for leg in flat_legs(spec) for k, v in leg.items()
                   if v is not None and k.endswith(("_min", "_max")) and k[:-4] not in known})


def needs_form(spec: dict) -> bool:
    return any(spec.get(f"{f}_min") is not None or spec.get(f"{f}_max") is not None
               for f in FORM_FILTERS)


def needs_sides(spec: dict) -> bool:
    # A form filter names a side: "the home team's last 5" read off the wrong
    # team is a different rule, and not a visibly different one.
    return (spec.get("side") in ("home", "away") or bool(spec.get("fav_status"))
            or bool(spec.get("home_team")) or bool(spec.get("away_team"))
            or any(spec.get(k) for k in TEAM_LISTS) or needs_form(spec))


def _named(team: str | None, names) -> bool:
    """Does `team` score as one of `names`? Alias-aware, same bar as a side."""
    return bool(team) and any(team_score(n, team) >= MIN_SIDE_SCORE for n in names or ())


def team_lists_fail(spec: dict, home: str | None, away: str | None) -> str | None:
    """Operator-written team lists (the Lab UI never produces them). A `_not_in`
    list is how a name that scores as two clubs is kept out: "Sporting Braga"
    scores 1.0 against "Sporting CP", so a big-three rule also names Braga as
    excluded."""
    for side, team in (("home", home), ("away", away)):
        inc, exc = spec.get(f"{side}_team_in"), spec.get(f"{side}_team_not_in")
        if inc and not _named(team, inc):
            return f"{side} team not in list"
        if exc and _named(team, exc):
            return f"{side} team excluded"
    return None


# ── a team's recent games ────────────────────────────────────────────────────

@dataclass(frozen=True)
class TeamForm:
    """What the backtest knows about a team at kick-off (`tf`, db/024 and db/083).

    rest_days       days since its previous league game
    form_pts5       league points in its previous 5
    avg_tg5         total goals a game in its previous 5
    avg_gf5/ga5     goals it scored / conceded a game in its previous 5
    venue_pts5      points in its previous 5 at this end: home if it is at home
                    today, away if it is away
    last_gd         its goal difference in its previous game
    *_run           games in a row before this one, up to RUN_CAP: won, without
                    defeat, lost, without a win

    Each is None where a NULL is in the backtest: 5 games or nothing, and a run
    the history ends inside of is not a run we can count."""
    rest_days: int | None = None
    form_pts5: int | None = None
    avg_tg5: float | None = None
    avg_gf5: float | None = None
    avg_ga5: float | None = None
    venue_pts5: int | None = None
    last_gd: int | None = None
    win_run: int | None = None
    unbeaten_run: int | None = None
    loss_run: int | None = None
    winless_run: int | None = None


def _pts(g) -> int:
    return 3 if g.goals_for > g.goals_against else 1 if g.goals_for == g.goals_against else 0


def _run(newest_first: list, counts) -> int | None:
    """Games in a row, newest first, that `counts` accepts, up to RUN_CAP. None
    when the history ends first: the run may be longer than we can see."""
    for i, g in enumerate(newest_first[:RUN_CAP]):
        if not counts(g):
            return i
    return RUN_CAP if len(newest_first) >= RUN_CAP else None


def form_of(games: list, kickoff: datetime, at_home: bool | None = None) -> TeamForm:
    """The backtest's definitions, over a team's finished games. Pure.

    Only games BEFORE this kick-off count, however the feed sorts them or
    whatever it has added since: a game being read after it started must not
    appear in its own history. `at_home` is where the team plays today, for the
    one figure that depends on it."""
    prev = sorted((g for g in games if g.kickoff < kickoff), key=lambda g: g.kickoff)
    if not prev:
        return TeamForm()
    # Calendar days, as `kickoff_utc::date - LAG(kickoff_utc::date)`.
    rest = (kickoff.astimezone(timezone.utc).date()
            - prev[-1].kickoff.astimezone(timezone.utc).date()).days
    newest = prev[::-1]
    known = dict(
        rest_days=rest,
        last_gd=prev[-1].goals_for - prev[-1].goals_against,
        win_run=_run(newest, lambda g: _pts(g) == 3),
        unbeaten_run=_run(newest, lambda g: _pts(g) > 0),
        loss_run=_run(newest, lambda g: _pts(g) == 0),
        winless_run=_run(newest, lambda g: _pts(g) < 3),
    )
    last5 = prev[-5:]
    if len(last5) < 5:
        return TeamForm(**known)
    venue = [g for g in prev if g.home is at_home][-5:] if at_home is not None else []
    return TeamForm(
        **known,
        form_pts5=sum(_pts(g) for g in last5),
        avg_tg5=round(sum(g.goals_for + g.goals_against for g in last5) / 5, 3),
        avg_gf5=round(sum(g.goals_for for g in last5) / 5, 3),
        avg_ga5=round(sum(g.goals_against for g in last5) / 5, 3),
        venue_pts5=sum(_pts(g) for g in venue) if len(venue) == 5 else None,
    )


def form_verdict(spec: dict, home: TeamForm | None, away: TeamForm | None) -> str | None:
    """None when both teams' form fits the spec; else which bound failed. An
    unknown value fails its bound, the way a NULL fails a WHERE clause in the
    backtest: "we could not read it" is never "it qualifies"."""
    for side, form in (("home", home), ("away", away)):
        for name in TEAM_FIGURES:
            lo, hi = spec.get(f"{side}_{name}_min"), spec.get(f"{side}_{name}_max")
            if lo is None and hi is None:
                continue
            value = getattr(form, name) if form else None
            if value is None:
                return f"{side} {name} unknown"
            if lo is not None and value < lo:
                return f"{side} {name} {value} < {lo}"
            if hi is not None and value > hi:
                return f"{side} {name} {value} > {hi}"
    return None


def team_history(code: str, team_id: str, kickoff: datetime, get=espn_stats.team_games) -> list | None:
    """A team's league games, reaching into last season only until the first
    RUN_CAP rounds or so have been played: form_of wants that many, and 5 at each
    end. None when ESPN did not answer: a partial history would read as a real,
    and wrong, number."""
    games = get(code, team_id)
    if games is None:
        return None
    prev = [g for g in games if g.kickoff < kickoff]
    if len(prev) >= RUN_CAP and all(sum(1 for g in prev if g.home is end) >= 5 for end in (True, False)):
        return games
    # Every league the Lab covers starts in the second half of the year.
    this_season = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    earlier = get(code, team_id, this_season - 1)
    if earlier is None:
        return None
    # [] is a club promoted this summer: its last season is in another league,
    # so it has no 5 here and a form rule simply does not fire on it yet.
    return earlier + games


# ── reading a Polymarket event ───────────────────────────────────────────────

def _jl(v) -> list:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def competition_code(event: dict) -> str | None:
    """The one Lab league this event belongs to, or None — including when its
    tags point at two different leagues, which is ambiguity, not a choice."""
    hits: set[str] = set()
    for t in event.get("tags") or []:
        slug = str(t.get("slug") or "").strip().lower()
        label = str(t.get("label") or "").strip().lower()
        for code, (slugs, labels) in PM_COMPETITIONS.items():
            if slug in slugs or label in labels:
                hits.add(code)
    return hits.pop() if len(hits) == 1 else None


def is_full_match_event(title: str) -> bool:
    """The main fixture event, or its "More Markets" sibling.

    Polymarket splits one fixture across sibling events — "A vs. B - Halftime
    Result", "- Exact Score", "- Second Half Result". Those carry questions a
    draw pattern matches happily ("Will the first half end in a draw?"), and a
    Lab spec is always about the full 90 minutes."""
    m = re.search(r"\s+-\s+(.+)$", title or "")
    return m is None or m.group(1).strip().lower() == "more markets"


def fixtures_of(events: list[dict]) -> list[dict]:
    """One event per fixture, with the markets of all its siblings on it.

    Polymarket splits a fixture: the moneyline on the main event, every goal line and
    both teams to score on "- More Markets". Read apart, "one bet per fixture" would
    depend on which sibling a bet was bought on. They share a pairing and a start. A
    league it cannot read keeps its own group and is skipped later, as it always was."""
    merged: dict[tuple, dict] = {}
    for raw in events:
        title = raw.get("title") or ""
        teams, ko = teams_from_title(title), kickoff_of(raw)
        if not is_full_match_event(title) or not teams or not ko:
            continue
        key = (teams, ko.date(), competition_code(raw))
        one = merged.setdefault(key, {**raw, "title": re.sub(r"\s+-\s+.*$", "", title).strip(), "markets": []})
        one["markets"] = one["markets"] + list(raw.get("markets") or [])
    return list(merged.values())


def teams_from_title(title: str) -> tuple[str, str] | None:
    clean = re.sub(r"\s+-\s+.*$", "", title or "").strip()
    m = _TITLE_RE.match(clean)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def kickoff_of(event: dict) -> datetime | None:
    raw = event.get("startTime") or next(
        (m.get("gameStartTime") for m in event.get("markets") or [] if m.get("gameStartTime")), None)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _leg(m: dict, outcome: str) -> dict | None:
    toks, outs, prices = _jl(m.get("clobTokenIds")), _jl(m.get("outcomes")), _jl(m.get("outcomePrices"))
    labels = [str(o).strip().lower() for o in outs]
    if len(toks) != len(labels) or outcome not in labels:
        return None
    i = labels.index(outcome)
    return {"token_id": str(toks[i]), "mid": _f(prices[i]) if i < len(prices) else None,
            "condition_id": m.get("conditionId"), "question": m.get("question") or "",
            "outcome": outs[i]}


def parse_markets(event: dict) -> dict:
    """The markets a Lab spec can buy: the three 1X2 legs, the goal lines in
    GOAL_LINES, and both teams to score.

    Families come from `sportsMarketType` and lines from `line`, never the question
    text: "… O/U 2.5 Corners" matches a goals pattern perfectly, and the first half,
    the second half and each team have totals families of their own. Over/Under and
    Yes/No come from the outcome LABELS, never their order."""
    out: dict = {"win": {}, "draw": None, BTTS: None, **{m: None for m in GOAL_LINES.values()}}
    for m in event.get("markets") or []:
        if m.get("closed") or m.get("active") is False:
            continue
        kind = str(m.get("sportsMarketType") or "").lower()
        q = str(m.get("question") or "").strip()
        rem = q.split(": ", 1)[1] if ": " in q else q
        if kind == "moneyline":
            leg = _leg(m, "yes")
            if leg is None:
                continue
            if _DRAW_RE.search(rem):
                out["draw"] = leg
            else:
                w = _WIN_RE.match(rem)
                if w:
                    out["win"][w.group("team").strip()] = leg
        elif kind == "totals":
            market = GOAL_LINES.get(_f(m.get("line")))
            over, under = _leg(m, "over"), _leg(m, "under")
            if market and over and under:
                out[market] = {"over": over, "under": under}
        elif kind == "both_teams_to_score":
            yes, no = _leg(m, "yes"), _leg(m, "no")
            if yes and no:
                out[BTTS] = {"yes": yes, "no": no}
    return out


def orientation(pm_home: str, pm_away: str, fixtures: list) -> tuple[str | None, object | None]:
    """'same' when Polymarket's first team is the real home side, 'swapped' when
    it is the away side, None when ESPN cannot say — a missing fixture, or names
    that fit both ways."""
    f = espn_stats.match_fixture(pm_home, pm_away, fixtures)
    if f is None:
        return None, None
    same = team_score(pm_home, f.home) >= MIN_SIDE_SCORE and team_score(pm_away, f.away) >= MIN_SIDE_SCORE
    swap = team_score(pm_home, f.away) >= MIN_SIDE_SCORE and team_score(pm_away, f.home) >= MIN_SIDE_SCORE
    if same and not swap:
        return "same", f
    if swap and not same:
        return "swapped", f
    return None, f


@dataclass
class EventView:
    code: str
    title: str
    kickoff: datetime
    markets: dict
    home: str | None = None          # the REAL home side, once ESPN confirmed it
    away: str | None = None
    espn: object | None = None       # ESPN's fixture, with its team ids


def _leg_for(team: str, legs: dict) -> dict | None:
    """The win leg whose question names `team` — alias-aware, and None on a tie."""
    scored = sorted(((team_score(name, team), leg) for name, leg in legs.items()),
                    key=lambda x: -x[0])
    if not scored or scored[0][0] < MIN_SIDE_SCORE:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None
    return scored[0][1]


def goal_label(market: str, side: str, title: str) -> str:
    if market == BTTS:
        return f"Both teams to score: {side.capitalize()} — {title}"
    return f"{side.capitalize()} {LINE_OF[market]} — {title}"


def fixture_condition_ids(markets: dict) -> list[str]:
    """Every market on this fixture a Lab spec can buy. "Has this agent already bet on
    this game?" is asked of all of them."""
    legs = list((markets.get("win") or {}).values()) + [markets.get("draw")]
    for market in GOAL_MARKETS:
        legs += list((markets.get(market) or {}).values())
    return sorted({x["condition_id"] for x in legs if x and x.get("condition_id")})


def pick(spec: dict, ev: EventView) -> tuple[dict | None, str, str]:
    """(leg to buy, outcome label, reason). The leg is None when the fixture does
    not fit; the reason says which condition failed, for --dry-run."""
    leagues = spec.get("leagues") or LAB_CODES
    if ev.code not in leagues:
        return None, "", "league"
    start = spec.get("season_start")
    if start is not None and start > CURRENT_SEASON:
        return None, "", "season"
    if needs_sides(spec) and ev.home is None:
        return None, "", "home/away not confirmed by ESPN"
    if spec.get("home_team") and team_score(spec["home_team"], ev.home) < MIN_SIDE_SCORE:
        return None, "", "home team"
    if spec.get("away_team") and team_score(spec["away_team"], ev.away) < MIN_SIDE_SCORE:
        return None, "", "away team"
    bad = team_lists_fail(spec, ev.home, ev.away)
    if bad:
        return None, "", bad

    side = spec.get("side")
    market = spec.get("market")
    if market in GOAL_MARKETS:
        # The replay at Polymarket's prices saw none of these leagues, and a goals market
        # backs no team, so a favourite takes no games there (bt_venue_fit) and none
        # here. O/U 2.5 keeps what its running agents were deployed on.
        if market != "ou25" and ev.code in NO_VENUE_HISTORY:
            return None, "", "league"
        if market != "ou25" and spec.get("fav_status"):
            return None, "", "favourite filter"
        legs = ev.markets.get(market)
        if not legs or side not in legs:
            return None, "", f"no {market}"
        return legs[side], goal_label(market, side, ev.title), "ok"

    if market != "1x2":
        return None, "", "market"
    if side == "draw":
        leg = ev.markets.get("draw")
        return (leg, f"Draw — {ev.title}", "ok") if leg else (None, "", "no draw leg")
    if side not in ("home", "away"):
        return None, "", "side"

    team, other = (ev.home, ev.away) if side == "home" else (ev.away, ev.home)
    leg = _leg_for(team, ev.markets.get("win") or {})
    if leg is None:
        return None, "", "no leg for the backed team"
    fav = spec.get("fav_status")
    if fav:
        opp = _leg_for(other, ev.markets.get("win") or {})
        if opp is None or leg.get("mid") is None or opp.get("mid") is None:
            return None, "", "favourite unknown"
        is_fav = leg["mid"] > opp["mid"]
        if (fav == "favorite") != is_fav:
            return None, "", "favourite filter"
    return leg, f"{team} to win — {ev.title}", "ok"


def book_verdict(book: dict | None, spec: dict) -> str | None:
    """None when the quote is a price this spec may buy at; else why not."""
    if not book:
        return "no two-sided book"
    ask, bid = book.get("best_ask"), book.get("best_bid")
    if ask is None or bid is None or not (MIN_ASK < ask < MAX_ASK):
        return "ask out of range"
    if ask - bid > MAX_SPREAD:
        return f"spread {ask - bid:.2f} > {MAX_SPREAD}"
    if (book.get("ask_depth_usd") or 0) < MIN_ASK_DEPTH_USD:
        return f"ask depth ${book.get('ask_depth_usd') or 0:.0f} < ${MIN_ASK_DEPTH_USD:.0f}"
    odds = 1.0 / ask
    lo, hi = spec.get("odds_min"), spec.get("odds_max")
    if lo is not None and odds < lo:
        return f"odds {odds:.2f} < {lo}"
    if hi is not None and odds > hi:
        return f"odds {odds:.2f} > {hi}"
    return None


# ── the network ──────────────────────────────────────────────────────────────

def fetch_upcoming(now: datetime) -> list[dict]:
    """Soccer events kicking off inside the entry window, not yet started.

    Bounded by `end_date_*` given as plain DATES. A fixture event's `endDate`
    equals its kick-off, so this is a kick-off filter in all but name; and Gamma
    silently drops negRisk match events when the bound is an ISO timestamp (see
    paper_trader.fetch_pm_markets_today). Unbounded, `order=startTime` opens on
    hundreds of stale, never-closed events from February — twelve pages in it
    had not reached September, so the first version of this saw 0 fixtures in a
    24-hour window with 863 on the board (2026-09-11). The window does the exact
    bounding, client-side."""
    horizon = now + timedelta(minutes=ENTRY_WINDOW_MIN)
    params = {
        "tag_slug": "soccer", "closed": "false", "limit": 100,
        "order": "startTime", "ascending": "true",
        "end_date_min": now.strftime("%Y-%m-%d"),
        "end_date_max": (horizon + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    out: list[dict] = []
    for page in range(GAMMA_PAGES):
        try:
            r = requests.get(f"{GAMMA}/events", params={**params, "offset": page * 100}, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:                        # noqa: BLE001
            log.warning(f"[lab_runner] gamma page {page}: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        for ev in batch:
            ko = kickoff_of(ev)
            if ko and now < ko <= horizon and not ev.get("live") and not ev.get("ended"):
                out.append(ev)
        last = kickoff_of(batch[-1])
        if len(batch) < 100 or (last and last > horizon):
            break
    return out


def _clob_winner(condition_id: str) -> dict[str, bool] | None:
    """{token_id: won} once the market has a winner, else None."""
    try:
        d = requests.get(f"{CLOB}/markets/{condition_id}", timeout=10).json()
    except Exception:                                   # noqa: BLE001
        return None
    toks = d.get("tokens") or []
    if not toks or not any(t.get("winner") for t in toks):
        return None
    return {str(t.get("token_id")): bool(t.get("winner")) for t in toks}


# ── the database ─────────────────────────────────────────────────────────────

def _conn():
    return db_txn.connect(DATABASE_URL)


def running_strategies(conn) -> list[dict]:
    """Running Lab specs. Any that cannot run live is paused here, with the
    reason written where its owner will see it."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Live rules (spec.kind='inplay') run on the pressure poll, in
        # lab_inplay_runner.py — this runner would read them as a pre-match
        # spec with no market and pause them.
        cur.execute("""SELECT id, name, theory, spec FROM strategies
                        WHERE source = 'lab' AND run_status = 'running' AND retired_at IS NULL
                          AND coalesce(spec->>'kind', '') <> 'inplay'""")
        rows = [dict(r) for r in cur.fetchall()]
    ok = []
    for r in rows:
        spec = r["spec"] if isinstance(r["spec"], dict) else json.loads(r["spec"] or "{}")
        r["spec"] = spec
        why = run_blocker(spec)
        if why:
            with conn.cursor() as cur:
                cur.execute("""UPDATE strategies SET run_status = 'paused', run_blocker = %s
                                WHERE id = %s""", (why, r["id"]))
            log.info(f"[lab_runner] paused #{r['id']}: {why}")
            continue
        ok.append(r)
    return ok


def already_in(conn, strategy_id: int, condition_ids: list[str]) -> bool:
    if not condition_ids:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM paper_trades pt JOIN pm_markets pm ON pm.id = pt.market_id
                        WHERE pt.strategy_id = %s AND pm.platform = 'polymarket'
                          AND pm.external_id = ANY(%s) LIMIT 1""", (strategy_id, condition_ids))
        return cur.fetchone() is not None


MARKET_TYPE = {BTTS: "btts", **{m: "over_under" for m in GOAL_LINES.values()}}


def write_trade(conn, strat: dict, ev: EventView, leg: dict, label: str, book: dict, market: str | None) -> int:
    ask = float(book["best_ask"])
    meta = {"event_title": ev.title, "competition": ev.code, "token_id": leg["token_id"],
            "outcome": leg["outcome"], "home": ev.home, "away": ev.away}
    reasoning = (
        f"Lab agent — \"{(strat.get('theory') or strat['name'])[:200]}\". {label}, "
        f"{ev.code}, kick-off {ev.kickoff:%Y-%m-%d %H:%M}Z. Bought at the CLOB ask "
        f"{ask:.3f} ({1 / ask:.2f}), bid {book['best_bid']:.3f}, ask depth "
        f"${book['ask_depth_usd']:.0f}. 1u flat, PAPER."
    )
    with db_txn.atomic(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pm_markets (platform, external_id, title, market_type,
                                        resolution_time, status, raw_metadata, ingested_at)
                VALUES ('polymarket', %s, %s, %s, %s, 'active', %s::jsonb, now())
                ON CONFLICT (platform, external_id) DO UPDATE
                   SET title = EXCLUDED.title, resolution_time = EXCLUDED.resolution_time
                RETURNING id""",
                (leg["condition_id"], leg["question"] or label,
                 MARKET_TYPE.get(market, "1x2"), ev.kickoff, json.dumps(meta)))
            market_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO paper_trades (strategy_id, market_id, outcome, entry_price, entry_odds,
                                          stake_units, reasoning, pm_token_id, pm_live, placed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false, now()) RETURNING id""",
                (strat["id"], market_id, label, ask, round(1 / ask, 4), STAKE_UNITS,
                 reasoning, leg["token_id"]))
            return cur.fetchone()[0]


# ── the cycle ────────────────────────────────────────────────────────────────

def forms_for(ev: EventView, cache: dict) -> tuple[TeamForm | None, TeamForm | None]:
    """(home, away) form for this fixture, each read once per cycle. None for a
    team ESPN gave no id or history for, which fails the rule (form_verdict)."""
    def one(team_id: str, at_home: bool) -> TeamForm | None:
        key = (ev.code, team_id, ev.kickoff)
        if key not in cache:
            games = team_history(ESPN_CODE[ev.code], team_id, ev.kickoff) if team_id else None
            cache[key] = None if games is None else form_of(games, ev.kickoff, at_home)
        return cache[key]
    fx = ev.espn
    if fx is None:
        return None, None
    return one(getattr(fx, "home_id", ""), True), one(getattr(fx, "away_id", ""), False)


def run_once(dry_run: bool = False, adhoc_spec: dict | None = None) -> int:
    conn = None
    if adhoc_spec is not None:
        # --spec: hold a spec against today's boards without a saved agent.
        # Always a dry run — an ad-hoc spec has no strategy row to trade under,
        # and so no need of the database.
        why = run_blocker(adhoc_spec)
        if why:
            log.info(f"[lab_runner] ad-hoc spec would be paused: {why}")
            return 0
        strategies = [{"id": 0, "name": "ad-hoc", "theory": None, "spec": adhoc_spec}]
        dry_run = True
    else:
        conn = _conn()
        strategies = running_strategies(conn)
    if not strategies:
        log.info("[lab_runner] no running Lab agents")
        return 0

    now = datetime.now(timezone.utc)
    events = fixtures_of(fetch_upcoming(now))
    espn_cache: dict[tuple, list] = {}
    form_cache: dict[tuple, TeamForm | None] = {}
    entered = considered = 0
    for raw in events:
        code = competition_code(raw)
        teams = teams_from_title(raw.get("title") or "")
        if not code or not teams:
            continue
        ev = EventView(code=code, title=raw.get("title") or "", kickoff=kickoff_of(raw),
                       markets=parse_markets(raw))
        if any(needs_sides(leg) for s in strategies for leg in flat_legs(s["spec"])) and code in ESPN_CODE:
            # ESPN's plain scoreboard is one match day. A --window past it has to ask by date.
            day = f"{ev.kickoff:%Y%m%d}" if ESPN_BY_DATE else None
            if (code, day) not in espn_cache:
                espn_cache[(code, day)] = espn_stats.fetch_league(ESPN_CODE[code], dates=day)
            orient, fixture = orientation(teams[0], teams[1], espn_cache[(code, day)])
            if orient == "same":
                ev.home, ev.away = teams
            elif orient == "swapped":
                ev.away, ev.home = teams
            if orient:
                ev.espn = fixture

        for strat, spec in ((s, one) for s in strategies for one in flat_legs(s["spec"])):
            leg, label, why = pick(spec, ev)
            if leg is None:
                continue
            considered += 1
            if conn is not None and already_in(conn, strat["id"], fixture_condition_ids(ev.markets)):
                continue
            # Last of the free checks, so a rule on a team's form costs ESPN
            # requests only on fixtures that fit everything else.
            if needs_form(spec):
                bad = form_verdict(spec, *forms_for(ev, form_cache))
                if bad:
                    log.info(f"[lab_runner] #{strat['id']} {label}: skip — {bad}")
                    continue
            book = _fetch_book({"token_id": leg["token_id"]})
            bad = book_verdict(book, spec)
            if bad:
                log.info(f"[lab_runner] #{strat['id']} {label}: skip — {bad}")
                continue
            if dry_run:
                log.info(f"[lab_runner] DRY #{strat['id']} would buy {label} at {book['best_ask']:.3f}")
                continue
            tid = write_trade(conn, strat, ev, leg, label, book, spec.get("market"))
            entered += 1
            log.info(f"[lab_runner] #{strat['id']} ENTERED pt#{tid} {label} at {book['best_ask']:.3f}")

    log.info(f"[lab_runner] {len(strategies)} running · {len(events)} fixtures in the window · "
             f"{considered} fits · {entered} entered")
    return entered


def settle() -> int:
    conn = _conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pt.id, pt.pm_token_id, pt.stake_units, pt.entry_odds, pm.external_id
              FROM paper_trades pt
              JOIN strategies s ON s.id = pt.strategy_id
              JOIN pm_markets pm ON pm.id = pt.market_id
             WHERE s.source = 'lab' AND pt.result IS NULL
               AND pm.resolution_time < now() - interval '2 hours'""")
        pending = [dict(r) for r in cur.fetchall()]
    # Every network call first, no transaction open (db_txn.py).
    winners = {cid: _clob_winner(cid) for cid in {p["external_id"] for p in pending}}
    settled = 0
    for p in pending:
        w = winners.get(p["external_id"])
        if not w or p["pm_token_id"] not in w:
            continue
        won = w[p["pm_token_id"]]
        with conn.cursor() as cur:
            cur.execute("""UPDATE paper_trades
                              SET result = %s,
                                  payout_units = CASE WHEN %s THEN stake_units * entry_odds ELSE 0 END,
                                  resolved_at = now()
                            WHERE id = %s AND result IS NULL""",
                        ("won" if won else "lost", won, p["id"]))
        settled += 1
    log.info(f"[lab_runner] settled {settled} of {len(pending)} finished trades")
    return settled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="one entry cycle (the default)")
    ap.add_argument("--dry-run", action="store_true", help="evaluate, write nothing")
    ap.add_argument("--settle", action="store_true", help="settle finished trades")
    ap.add_argument("--spec", help="a Lab spec as JSON, dry-run against today's boards")
    ap.add_argument("--window", type=int, help=f"minutes ahead to consider (default {ENTRY_WINDOW_MIN})")
    args = ap.parse_args()
    # force: late_goals_observer configures the root logger on import, and
    # without it every line here would carry that module's "[late_goals]" tag.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S", force=True)
    if args.window:
        globals().update(ENTRY_WINDOW_MIN=args.window, ESPN_BY_DATE=True)
    if args.settle:
        settle()
    else:
        run_once(dry_run=args.dry_run, adhoc_spec=json.loads(args.spec) if args.spec else None)


if __name__ == "__main__":
    main()
