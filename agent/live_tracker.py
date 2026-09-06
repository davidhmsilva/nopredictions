"""
LiveMatchTracker — polls api-football for in-game stats and builds
rolling-window pressure signals.

Tracks stat snapshots every poll cycle. Calculates deltas over configurable
windows (default: last 15 min) to detect momentum shifts:

  - Shot pressure: shots on target / shots inside box in window
  - xG pressure: cumulative xG delta in window
  - Territorial dominance: possession + corners in window
  - Danger index: composite pressure score (0–100)

Usage:
    tracker = LiveMatchTracker()
    tracker.poll()                          # fetch + store snapshot
    signals = tracker.get_signals(fixture_id)  # pressure signals
    all_signals = tracker.get_all_signals()    # all tracked matches
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

import af_budget

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

log = logging.getLogger(__name__)

PRESSURE_WINDOW_MIN = 15
MIN_MINUTE_FOR_SIGNALS = 15
# api-football drops a fixture from the live feed briefly at half time on some
# competitions. Dropping its snapshot history on the first miss would discard
# the window baseline exactly when the second half starts.
MISSING_POLLS_BEFORE_DROP = 20


# The hand-written weights. xG carries 40% of the score and api-football supplies
# it on only about 60% of the fixtures it covers with statistics at all.
_W_XG, _W_SHOTS_ON, _W_SHOTS_IN, _W_CORNERS, _W_POSS = 0.40, 0.25, 0.15, 0.10, 0.10


def danger_index(shots_on: float, shots_inside: float, xg: float,
                 corners: float, possession: float,
                 has_xg: bool = True) -> float:
    """
    Composite danger score 0–100.
    Weights: xG(40%) + shots_on(25%) + shots_inside(15%) + corners(10%) + possession(10%)

    The inputs are counts over a 15-MINUTE window. Anything measuring pressure
    over a different span has to scale to that first, or the score is not on the
    same axis as everyone else's — the whole point of one definition is that a
    threshold like "pressure >= 45" means the same thing to every caller.

    `has_xg=False` says the FEED carries no xG for this fixture, which is a very
    different statement from "no chances were created". Without renormalising,
    such a fixture can only ever score 60 out of 100, and measured against a
    threshold calibrated on the mixed population it is not merely penalised — it
    is excluded: on 165 real openings, 13.2% of fixtures WITH xG cleared 25 and
    0.0% of those without did, at a p90 of 15.3 against 26.1. That gap is the
    missing weight, not a difference in how the matches were played. So the
    remaining weights are rescaled to sum to 1.

    This assumes the missing term would have been proportional to the others,
    which is an assumption and not a fact — xG correlates with shots inside the
    box and on target, but it is not those. Every consumer stores has_xg on the
    row so a later fit can control for it instead of treating two different
    measurements as one.

    Module level, and not a method, because the weights are the object under
    test: they were written by hand and never estimated, and two copies of them
    drifting apart would quietly split the evidence for refitting them.
    """
    xg_score = min(100, xg * 100)                      # 1.0 xG in window = 100
    shots_on_score = min(100, shots_on * 20)           # 5 shots on target = 100
    shots_inside_score = min(100, shots_inside * 15)   # ~7 shots inside = 100
    corners_score = min(100, corners * 15)             # ~7 corners = 100
    poss_score = max(0, (possession - 30) / 40 * 100)  # 30%=0, 70%=100

    score = (
        shots_on_score * _W_SHOTS_ON +
        shots_inside_score * _W_SHOTS_IN +
        corners_score * _W_CORNERS +
        poss_score * _W_POSS
    )
    if not has_xg:
        return score / (1.0 - _W_XG)
    return score + xg_score * _W_XG


@dataclass
class StatSnapshot:
    minute: int
    timestamp: float
    home_shots_on: int = 0
    away_shots_on: int = 0
    home_shots_total: int = 0
    away_shots_total: int = 0
    home_shots_inside: int = 0
    away_shots_inside: int = 0
    home_corners: int = 0
    away_corners: int = 0
    home_possession: float = 50.0
    away_possession: float = 50.0
    home_xg: float = 0.0
    away_xg: float = 0.0
    home_goals: int = 0
    away_goals: int = 0
    home_reds: int = 0
    away_reds: int = 0
    home_yellows: int = 0
    away_yellows: int = 0
    home_gk_saves: int = 0
    away_gk_saves: int = 0
    home_dangerous_attacks: int = 0
    away_dangerous_attacks: int = 0
    # When the stat block below was actually true. A poll that does not re-fetch
    # carries the previous values forward (see _carry_stats_forward), so these
    # say how old they are — without them a 16th-minute row and the 13th-minute
    # numbers inside it are indistinguishable.
    stats_minute: int | None = None
    stats_fetched_at: float | None = None


@dataclass
class PressureSignals:
    fixture_id: int
    home: str
    away: str
    minute: int
    score: str
    # Carried on the signal because every consumer writes it to a row and none
    # of them can reach fixture_info. Without it the observation tables cannot
    # answer "which competitions are we actually measuring" — which is the first
    # question anyone asks of a coverage problem.
    league: str | None = None
    # The minute the stat block was actually true, which is not `minute` on a
    # poll that reused the previous fetch (ENRICH_TTL_S). Anything turning totals
    # into a per-minute rate must divide by THIS, or a 16th-minute reading of
    # 13th-minute numbers reads 20% quieter than the match was.
    stats_minute: int | None = None
    # Raw deltas over the window
    home_shots_on_window: int = 0
    away_shots_on_window: int = 0
    home_shots_inside_window: int = 0
    away_shots_inside_window: int = 0
    home_xg_window: float = 0.0
    away_xg_window: float = 0.0
    home_corners_window: int = 0
    away_corners_window: int = 0
    # Current totals
    home_xg_total: float = 0.0
    away_xg_total: float = 0.0
    home_possession: float = 50.0
    away_possession: float = 50.0
    home_reds: int = 0
    away_reds: int = 0
    home_shots_on_total: int = 0
    away_shots_on_total: int = 0
    home_shots_total: int = 0
    away_shots_total: int = 0
    home_shots_inside_total: int = 0
    away_shots_inside_total: int = 0
    home_corners_total: int = 0
    away_corners_total: int = 0
    home_goals: int = 0
    away_goals: int = 0
    # False when there was no snapshot near (minute - window) and the window
    # deltas below are the match totals scaled down instead of a real delta.
    # A consumer that treats the fallback as a delta reads steady play as a
    # surge, so it has to be able to tell the two apart.
    has_window: bool = False
    # False when api-football has no statistics coverage for this competition —
    # which is most of the smaller leagues it reports as live. Every stat then
    # sits at zero and the danger index collapses to its possession term (~5),
    # which is indistinguishable from a genuinely dead match. Anything reading
    # pressure MUST check this first, or a no-coverage fixture enters the record
    # as strong evidence that low pressure precedes no goal.
    has_stats: bool = False
    # True when the window baseline and the latest snapshot hold the SAME fetch,
    # so every delta below is structurally zero regardless of how the match is
    # being played. Distinct from has_stats (there IS a stat block) and from
    # has_window (a baseline was found): this says the two carry one measurement
    # between them, which is no measurement of a window at all.
    stats_frozen: bool = False
    # Composite scores (0–100)
    home_danger_index: float = 0.0
    away_danger_index: float = 0.0
    # Strategy signals
    xg_overperformance_home: float = 0.0  # goals - xG (positive = lucky)
    xg_overperformance_away: float = 0.0
    pressure_without_goals_home: bool = False  # high xG window but no goals
    pressure_without_goals_away: bool = False
    shot_dominance_home: float = 0.0  # ratio of shots in window
    shot_dominance_away: float = 0.0

    def summary(self) -> str:
        lines = [
            f'{self.home} {self.score} {self.away} ({self.minute}\')',
            f'  Danger: H={self.home_danger_index:.0f} A={self.away_danger_index:.0f}',
            f'  Window ({PRESSURE_WINDOW_MIN}min): '
            f'shots_on H={self.home_shots_on_window} A={self.away_shots_on_window} | '
            f'xG H={self.home_xg_window:.2f} A={self.away_xg_window:.2f}',
            f'  Total xG: H={self.home_xg_total:.2f} A={self.away_xg_total:.2f} | '
            f'Poss: H={self.home_possession:.0f}% A={self.away_possession:.0f}%',
        ]
        if self.pressure_without_goals_home:
            lines.append(f'  ⚠️  {self.home} pressing hard without scoring (xG window={self.home_xg_window:.2f})')
        if self.pressure_without_goals_away:
            lines.append(f'  ⚠️  {self.away} pressing hard without scoring (xG window={self.away_xg_window:.2f})')
        over_h = self.xg_overperformance_home
        over_a = self.xg_overperformance_away
        if abs(over_h) > 0.5:
            tag = 'overperforming' if over_h > 0 else 'underperforming'
            lines.append(f'  📊 {self.home} {tag} xG by {over_h:+.2f}')
        if abs(over_a) > 0.5:
            tag = 'overperforming' if over_a > 0 else 'underperforming'
            lines.append(f'  📊 {self.away} {tag} xG by {over_a:+.2f}')
        return '\n'.join(lines)


# ── enrichment budget ────────────────────────────────────────────────────────
# /fixtures?live=all does not carry statistics for most fixtures, so each one
# needs its own /fixtures/statistics call. Polling every 60s and enriching every
# live fixture costs ~90 calls a cycle = ~130k/day against a 75,000/day account
# shared with 13 other scripts. We hit that wall daily, and because
# _enrich_fixture used to swallow every failure into `return False`, the agent
# labelled the result "no api-football stats coverage" — 36,917 rows on
# 2026-08-15 alone. It was never a coverage problem. It was the budget.
#
# So: spend the calls where they can actually produce a decision (the caller
# ranks fixtures), never re-fetch the same fixture inside the TTL, stop calling
# a competition that has genuinely proven uncovered, and stop the moment the
# API says the day is spent instead of burning the rest of the cycle on calls
# that cannot succeed.
# Worst case is 40*1440 + 1440 live calls = 59k/day against a 75k Ultra limit,
# and the real figure is far below it because ENRICH_TTL_S throttles each fixture
# to one call per three cycles: measured usage over ~4,000 cycles was 3,246 stats
# calls, under one per cycle. The budget was never what bound us at 25 either —
# it is raised here so that spare capacity can reach fixtures outside the trading
# universe (see the callers' priority functions), not because 25 was exhausted.
ENRICH_BUDGET_PER_CYCLE = 40
ENRICH_TTL_S = 180              # in-game stats do not move fast enough to beat this
NO_COVERAGE_STRIKES = 3         # distinct fixtures returning EMPTY before we blacklist

# The two ways api-football says no, kept apart because they mean opposite
# things about whether more budget would help. Per-minute is a burst we caused
# and recover from within a cycle; daily means the allowance is gone and no
# amount of ranking or budget changes anything until midnight.
RATE_LIMITED = 'rate limited (per-minute)'
DAILY_EXHAUSTED = 'daily quota exhausted'
# How long a league stays blacklisted by the strike heuristic. It used to be
# forever — the dict was never cleared — and this process runs for weeks. On
# 2026-08-20 that had MLS blacklisted as "uncovered" while api-football was
# serving 5 shots to 8 for the very fixtures we refused to ask about; the agent
# recorded "no api-football stats coverage" for hours, and the restart alone
# fixed it. A heuristic that cannot recover is not a heuristic, it is a latch.
EMPTY_LEAGUE_TTL_S = 2 * 3600


# Everything the statistics endpoint fills in. Goals, minute and cards are NOT
# here: those come fresh from the live feed on every poll and must never be
# carried forward.
_STAT_FIELDS = (
    'home_shots_on', 'away_shots_on', 'home_shots_total', 'away_shots_total',
    'home_shots_inside', 'away_shots_inside', 'home_corners', 'away_corners',
    'home_possession', 'away_possession', 'home_xg', 'away_xg',
    'home_gk_saves', 'away_gk_saves',
    'home_dangerous_attacks', 'away_dangerous_attacks',
)


def snapshot_has_stats(snap: 'StatSnapshot') -> bool:
    """Any non-zero counter proves the stat block was filled.

    Possession is excluded on purpose: it defaults to 50.0, so testing it would
    call every empty snapshot populated.
    """
    return any((snap.home_shots_total, snap.away_shots_total, snap.home_shots_on,
                snap.away_shots_on, snap.home_corners, snap.away_corners,
                snap.home_xg, snap.away_xg))


def _carry_stats_forward(snap: 'StatSnapshot', prev: 'StatSnapshot | None') -> None:
    """Keep the last known stats on a poll that did not re-fetch them.

    Every poll builds a NEW snapshot, and only the fixtures that win a paid call
    get theirs filled. With ENRICH_TTL_S at 180s against a 60s cycle that is one
    poll in three — so two polls out of three used to report a live match as
    having no statistics at all: shots 0-0, no pressure, no measurement, on a
    fixture we had measured sixty seconds earlier.

    Traced on 2026-08-20 across every fixture in the first-half agents' entry
    window; it is why they made zero entries. The rolling-window deltas in the
    full-match arm were hit too — max(0, 0 - 5) is 0, so its danger index
    collapsed on the same two thirds of cycles.

    A snapshot is the last known state of the match, not a record of what we
    happened to fetch this second. The age is carried with it so a consumer can
    tell the difference.
    """
    if snapshot_has_stats(snap) or prev is None or not snapshot_has_stats(prev):
        return
    for field in _STAT_FIELDS:
        setattr(snap, field, getattr(prev, field))
    snap.stats_minute = prev.stats_minute
    snap.stats_fetched_at = prev.stats_fetched_at


class LiveMatchTracker:
    def __init__(self, window_minutes: int = PRESSURE_WINDOW_MIN):
        self.api_key = os.getenv('FOOTBALL_API_KEY', '')
        self.window_minutes = window_minutes
        self.snapshots: dict[int, list[StatSnapshot]] = defaultdict(list)
        self.fixture_info: dict[int, dict] = {}
        self._missing: dict[int, int] = {}
        self._last_poll: float = 0
        # enrichment bookkeeping
        self._enrich_at: dict[int, float] = {}          # fid -> last attempt
        self._empty_leagues: dict[str, set] = defaultdict(set)  # league -> fids seen empty
        self._empty_league_at: dict[str, float] = {}     # league -> first strike, for expiry
        # league id -> does api-football publish match statistics for it?
        # This is a FACT the API will tell us (/leagues -> coverage.fixtures.
        # statistics_fixtures), not something to infer from empty responses.
        self._league_stats_coverage: dict[int, bool | None] = {}
        # Set when the API refuses on a limit. Two very different refusals arm
        # it — a per-minute burst (seconds of backoff) and a spent daily
        # allowance (the rest of the day) — so the REASON is carried alongside
        # the deadline. Recording one label for both is what made the 176 rows
        # marked "api quota spent" undiagnosable: the daily allowance was 3%
        # used at the time, so every one of them was almost certainly a
        # per-minute blip, but nothing in the record could prove it.
        self._quota_spent_until: float = 0                # epoch
        # Whether the last poll failed before it could reach api-football at all.
        # A caller running forever needs this: the handler below logs and
        # swallows, so a transport fault that never clears is indistinguishable
        # from a quiet afternoon with no live football.
        self.last_poll_failed: bool = False
        self._quota_reason: str = RATE_LIMITED            # which limit armed it
        # Why each fixture has no stats this cycle. The agent records this
        # verbatim, so a row can never again claim "no coverage" when the real
        # answer was "we did not ask".
        self.enrich_status: dict[int, str] = {}
        self.last_enrich_report: dict[str, int] = {}
        # Every call this process makes, counted as it is made. Three years of
        # "is it our quota?" have been argued from log-line proxies; this is the
        # number itself. See af_budget for why each process writes its own file.
        self.calls = af_budget.process_counter()

    def poll(self, priority=None) -> dict[int, PressureSignals]:
        """
        Fetch all live fixtures, store stat snapshots, return pressure signals.

        `priority(fid, snapshot) -> float` lets the caller rank which fixtures
        are worth a paid /fixtures/statistics call this cycle; return a negative
        rank to skip one outright. Without it every eligible fixture competes
        equally for ENRICH_BUDGET_PER_CYCLE calls.
        """
        if not self.api_key:
            log.warning('[tracker] No FOOTBALL_API_KEY set')
            return {}

        now = time.time()

        try:
            # Fetch live fixtures
            self.calls.record('live')
            resp = requests.get(
                'https://v3.football.api-sports.io/fixtures',
                params={'live': 'all'},
                headers={'x-apisports-key': self.api_key},
                timeout=10,
            )
            # The socket itself worked. Cleared on the response and not at the
            # end of the poll, so an HTTP error — which restarting cannot fix —
            # is never mistaken for the transport being gone.
            self.last_poll_failed = False
            if resp.status_code != 200:
                log.warning(f'[tracker] api-football HTTP {resp.status_code}')
                return {}

            body = resp.json()
            # A REFUSAL ARRIVES AS HTTP 200 WITH AN EMPTY `response`.
            #
            # Without this branch it read as "0 live fixtures" — a perfectly
            # healthy-looking line, printed once a minute, for three evenings
            # running (2026-08-31 dark from 23:40, 09-01 from 21:14, 09-02 from
            # 20:42). The agent recorded nothing, the log said nothing was
            # wrong, and only pressure_health.py's STALE line — which nobody
            # reads — showed it. `_enrich_fixture` twenty lines below has
            # checked `errors` on its own calls all along; the live poll never
            # did, so the one call every cycle depends on was the one call with
            # no error handling.
            #
            # The response headers are logged with it because api-football
            # contradicts itself here: on 2026-09-02 it refused every endpoint
            # with "request limit for the day" while reporting
            # x-ratelimit-requests-remaining: 74999 of 75000, against ~2,300
            # calls actually made. Neither number is evidence on its own.
            errors = body.get('errors') or {}
            if errors:
                blob = str(errors).lower()
                daily = 'day' in blob
                self._quota_spent_until = time.time() + (3600 if daily else 60)
                self._quota_reason = DAILY_EXHAUSTED if daily else RATE_LIMITED
                quota = {k: v for k, v in resp.headers.items()
                         if 'ratelimit' in k.lower()}
                log.error(
                    f'[tracker] api-football REFUSED the live poll: '
                    f'{str(errors)[:200]} | headers {quota} -> '
                    f'{self._quota_reason}, no fixtures this cycle')
                return {}

            fixtures = body.get('response', [])
            log.info(f'[tracker] {len(fixtures)} live fixtures')

            fixture_ids_with_stats = []
            # Fixtures whose stats came from THIS poll's feed. The enrichment
            # gate below must key on that and never on the snapshot's contents:
            # _carry_stats_forward fills those in from the previous poll, so a
            # snapshot holding stats proves nothing about whether we just saw
            # them. See the gate for what that cost.
            inline_stats: set[int] = set()

            for f in fixtures:
                fid = f['fixture']['id']
                home = f['teams']['home']['name']
                away = f['teams']['away']['name']
                elapsed = f['fixture']['status'].get('elapsed', 0)
                goals_h = f['goals'].get('home', 0) or 0
                goals_a = f['goals'].get('away', 0) or 0
                league = f.get('league', {}).get('name', '?')
                league_id = f.get('league', {}).get('id')

                self.fixture_info[fid] = {
                    'home': home, 'away': away,
                    'league': league,
                    'league_id': league_id,
                    'fixture_id': fid,
                }

                # Build snapshot from inline statistics if available
                snap = StatSnapshot(
                    minute=elapsed or 0,
                    timestamp=time.time(),
                    home_goals=goals_h,
                    away_goals=goals_a,
                )

                # Extract red/yellow cards from events
                for ev in f.get('events', []):
                    if ev.get('type') == 'Card':
                        team_name = ev.get('team', {}).get('name', '')
                        if ev.get('detail') == 'Red Card':
                            if team_name == home:
                                snap.home_reds += 1
                            elif team_name == away:
                                snap.away_reds += 1
                        elif 'Yellow' in (ev.get('detail') or ''):
                            if team_name == home:
                                snap.home_yellows += 1
                            elif team_name == away:
                                snap.away_yellows += 1

                # Inline statistics (some fixtures include them)
                for team_stats in f.get('statistics', []):
                    is_home = team_stats.get('team', {}).get('name') == home
                    self._parse_stats(snap, team_stats.get('statistics', []), is_home)

                if snapshot_has_stats(snap):
                    # Inline statistics arrived with the live feed itself.
                    snap.stats_minute, snap.stats_fetched_at = snap.minute, now
                    inline_stats.add(fid)
                else:
                    _carry_stats_forward(
                        snap, self.snapshots[fid][-1] if self.snapshots[fid] else None)
                self.snapshots[fid].append(snap)
                fixture_ids_with_stats.append(fid)

            # Fetch detailed stats for fixtures that need them, cheapest-first.
            # `priority` is supplied by the caller because only the caller knows
            # which fixtures can still produce a decision; the tracker must not
            # guess. Higher rank is served first, negative means "do not spend".
            self.enrich_status = {}
            report = Counter()
            candidates = []
            for fid in fixture_ids_with_stats:
                latest = self.snapshots[fid][-1]
                if fid in inline_stats:
                    continue                                  # inline stats were enough
                # NOT `if latest.home_shots_total`: from 2026-08-20 to 08-25 that
                # is what stood here, and once _carry_stats_forward landed it read
                # the carried copy of an earlier fetch as proof that this poll had
                # stats. One fetch per fixture was all any match ever got: totals
                # froze, and fifteen minutes later the rolling window was
                # differencing a carry against its own source, so every delta went
                # to exactly 0 and the danger index locked at its possession term
                # (5.0) for the rest of the match. has_stats and has_window both
                # said True throughout, so the rows read as measurements of a dead
                # game rather than as the absence of a measurement. Strategy 16
                # made 1 entry in four days against a gate of 45.
                if latest.minute <= 5:
                    report['too early'] += 1
                    continue
                info = self.fixture_info.get(fid, {}) or {}
                league, league_id = info.get('league', ''), info.get('league_id')
                covered = self.league_has_stats(league_id)
                if covered is False:
                    self.enrich_status[fid] = 'league has no stats coverage (api)'
                    report['league uncovered (api)'] += 1
                    continue
                # Only guess when the API would not say. A league it CONFIRMS is
                # covered is never blacklisted on empty responses — those are far
                # more often "not published yet" than "never will be".
                if covered is None and self._league_blacklisted(league):
                    self.enrich_status[fid] = 'league proven uncovered'
                    report['league uncovered'] += 1
                    continue
                if now - self._enrich_at.get(fid, 0) < ENRICH_TTL_S:
                    self.enrich_status[fid] = 'within TTL, using last fetch'
                    report['within TTL'] += 1
                    continue
                rank = priority(fid, latest) if priority else 0
                if rank < 0:
                    # The caller may have written a more specific reason on its
                    # way to returning -1, and it is the only one that knows the
                    # difference — "past the last minute we could act on" and
                    # "inside the window but sampled out to protect the evening
                    # allowance" are opposite facts about the same missing row.
                    # Collapsing two reasons into one label is the mistake this
                    # file has now paid for three times.
                    named = self.enrich_status.get(fid)
                    self.enrich_status[fid] = named or 'not worth a call this cycle'
                    report[named or 'deprioritised'] += 1
                    continue
                candidates.append((rank, fid, latest))

            candidates.sort(key=lambda t: -t[0])
            for rank, fid, latest in candidates[:ENRICH_BUDGET_PER_CYCLE]:
                # No call is made here — the latch is still holding from an
                # earlier refusal, so this fixture is being labelled on the
                # strength of something that happened to a DIFFERENT fixture.
                # That is how one refusal became a whole cycle of rows.
                if now < self._quota_spent_until:
                    self.enrich_status[fid] = self._quota_reason
                    report[self._quota_reason] += 1
                    continue
                self._enrich_at[fid] = now
                status = self._enrich_fixture(fid, latest)
                self.enrich_status[fid] = status
                report[status] += 1
            for rank, fid, latest in candidates[ENRICH_BUDGET_PER_CYCLE:]:
                self.enrich_status[fid] = 'over cycle budget'
                report['over budget'] += 1

            self.last_enrich_report = dict(report)
            if report:
                log.info('[tracker] enrich: ' + ', '.join(
                    f'{k}={v}' for k, v in sorted(report.items(), key=lambda kv: -kv[1])))

            self._last_poll = time.time()

            # Only the fixtures that are live RIGHT NOW. get_all_signals() walks
            # everything ever tracked, and self.snapshots is never emptied, so
            # returning it means a match that finished hours ago keeps being
            # reported every cycle with its final snapshot — frozen minute,
            # frozen score, forever. A consumer recording one row per signal
            # would fill its table with copies of a match nobody is playing.
            live_now = set(fixture_ids_with_stats)
            self._prune(live_now)
            return {fid: sig for fid, sig in self.get_all_signals().items()
                    if fid in live_now}

        except Exception as e:
            self.last_poll_failed = True
            log.error(f'[tracker] Poll error: {e}')
            return {}
        finally:
            # In a finally so the REFUSAL path reports too. The last four
            # blackouts were all diagnosed after the fact from a log that never
            # said what we had spent when the door closed — which is the one
            # number that decides whether the allowance was ours to lose.
            af_budget.log_status(log, self.calls, tag='tracker',
                                 force=time.time() < self._quota_spent_until)

    def league_has_stats(self, league_id: int | None) -> bool | None:
        """Does api-football publish match statistics for this competition?

        True / False from the API's own `coverage.fixtures.statistics_fixtures`,
        None when we could not find out. One call per competition per process —
        coverage does not change mid-season — against a heuristic that had to
        burn three fixtures to guess, could be wrong, and never recovered.
        """
        if not league_id:
            return None
        if league_id in self._league_stats_coverage:
            return self._league_stats_coverage[league_id]

        answer = None
        try:
            self.calls.record('leagues')
            resp = requests.get(
                'https://v3.football.api-sports.io/leagues',
                params={'id': league_id},
                headers={'x-apisports-key': self.api_key},
                timeout=8,
            )
            if resp.status_code == 200 and not (resp.json().get('errors') or {}):
                for entry in resp.json().get('response', []):
                    seasons = [x for x in entry.get('seasons', []) if x.get('current')]
                    if not seasons:
                        continue
                    cov = (seasons[0].get('coverage') or {}).get('fixtures') or {}
                    answer = bool(cov.get('statistics_fixtures'))
        except Exception:
            answer = None

        # A failed lookup is cached as None and retried on the next process, not
        # hammered every cycle; an answer is cached for good.
        self._league_stats_coverage[league_id] = answer
        return answer

    def _league_blacklisted(self, league: str) -> bool:
        """The strike heuristic, now with an expiry — and only ever consulted
        when the API could not answer for itself."""
        if len(self._empty_leagues.get(league, ())) < NO_COVERAGE_STRIKES:
            return False
        if time.time() - self._empty_league_at.get(league, 0) > EMPTY_LEAGUE_TTL_S:
            self._empty_leagues.pop(league, None)
            self._empty_league_at.pop(league, None)
            return False
        return True

    def _prune(self, live_now: set[int]) -> None:
        """Forget fixtures that have dropped off the live feed.

        Kept for a few cycles first: api-football drops a fixture briefly at
        half time on some competitions, and discarding its history immediately
        would throw away the window baseline that makes the deltas meaningful.
        """
        for fid in list(self.snapshots):
            if fid in live_now:
                self._missing[fid] = 0
                continue
            self._missing[fid] = self._missing.get(fid, 0) + 1
            if self._missing[fid] > MISSING_POLLS_BEFORE_DROP:
                del self.snapshots[fid]
                self.fixture_info.pop(fid, None)
                del self._missing[fid]

    def _enrich_fixture(self, fid: int, snap: StatSnapshot) -> str:
        """Fetch detailed statistics for one fixture.

        Returns WHY it went the way it did, never a bare bool. The distinction
        that matters is 'empty' (this competition really has no stats) versus
        RATE_LIMITED / DAILY_EXHAUSTED / 'http' / 'error' (we failed to ask).
        Collapsing those into False is what produced tens of thousands of rows
        mislabelled as uncovered; collapsing the first two into each other is
        what made the remainder undiagnosable.
        """
        try:
            self.calls.record('stats')
            resp = requests.get(
                'https://v3.football.api-sports.io/fixtures/statistics',
                params={'fixture': fid},
                headers={'x-apisports-key': self.api_key},
                timeout=8,
            )
            if resp.status_code == 429:
                log.warning(
                    f'[tracker] 429 on /fixtures/statistics fixture={fid} — '
                    f'backing off 300s. body={resp.text[:200]!r}')
                self._quota_spent_until = time.time() + 300
                self._quota_reason = RATE_LIMITED
                return RATE_LIMITED
            if resp.status_code != 200:
                return f'http {resp.status_code}'

            body = resp.json()
            # api-football answers 200 with an errors object when the plan's
            # daily or per-minute allowance is gone. Treated as success, this
            # reads as "no stats" for every fixture for the rest of the day.
            errors = body.get('errors') or {}
            if errors:
                blob = str(errors).lower()
                if 'limit' in blob or 'rate' in blob:
                    # Day is spent: stop asking. Per-minute: back off briefly.
                    # The blob is LOGGED because it is the only evidence of
                    # which one this was, and the branch below turns it into an
                    # hour of silence or a minute of it.
                    daily = 'day' in blob
                    self._quota_spent_until = time.time() + (3600 if daily else 60)
                    self._quota_reason = DAILY_EXHAUSTED if daily else RATE_LIMITED
                    log.warning(
                        f'[tracker] api-football refused on a limit '
                        f'(fixture={fid}): {str(errors)[:200]} -> '
                        f'{self._quota_reason}, backing off '
                        f'{3600 if daily else 60}s')
                    return self._quota_reason
                log.warning(f'[tracker] api-football errors (fixture={fid}): '
                            f'{str(errors)[:200]}')
                return 'error'

            stats = body.get('response', [])
            if not stats:
                info = self.fixture_info.get(fid, {}) or {}
                league, league_id = info.get('league', ''), info.get('league_id')
                # An empty answer from a league the API says it covers means the
                # stats are not published YET (it is minute 8 of a small fixture),
                # not that the competition is uncovered. Striking it would latch a
                # covered league off for the life of the process.
                if league and self.league_has_stats(league_id) is not True:
                    self._empty_leagues[league].add(fid)
                    self._empty_league_at.setdefault(league, time.time())
                return 'empty'

            info = self.fixture_info.get(fid, {})
            home = info.get('home', '')

            for team_stats in stats:
                is_home = team_stats.get('team', {}).get('name') == home
                self._parse_stats(snap, team_stats.get('statistics', []), is_home)

            snap.stats_minute, snap.stats_fetched_at = snap.minute, time.time()
            return 'ok'
        except Exception as exc:
            return f'error {type(exc).__name__}'

    def _parse_stats(self, snap: StatSnapshot, stats: list[dict], is_home: bool):
        """Parse api-football statistics array into a snapshot."""
        for s in stats:
            typ = s.get('type', '')
            val = s.get('value')
            if val is None:
                continue

            if typ == 'Shots on Goal':
                if is_home: snap.home_shots_on = int(val)
                else: snap.away_shots_on = int(val)
            elif typ == 'Total Shots':
                if is_home: snap.home_shots_total = int(val)
                else: snap.away_shots_total = int(val)
            elif typ == 'Shots insidebox':
                if is_home: snap.home_shots_inside = int(val)
                else: snap.away_shots_inside = int(val)
            elif typ == 'Corner Kicks':
                if is_home: snap.home_corners = int(val)
                else: snap.away_corners = int(val)
            elif typ == 'Ball Possession':
                pct = float(str(val).replace('%', ''))
                if is_home: snap.home_possession = pct
                else: snap.away_possession = pct
            elif typ == 'expected_goals':
                if is_home: snap.home_xg = float(val)
                else: snap.away_xg = float(val)
            elif typ == 'Goalkeeper Saves':
                if is_home: snap.home_gk_saves = int(val)
                else: snap.away_gk_saves = int(val)
            elif typ == 'Red Cards':
                if is_home: snap.home_reds = int(val)
                else: snap.away_reds = int(val)
            elif typ == 'Yellow Cards':
                if is_home: snap.home_yellows = int(val)
                else: snap.away_yellows = int(val)

    def _find_window_start(self, snapshots: list[StatSnapshot], current_minute: int) -> StatSnapshot | None:
        """Find the snapshot closest to (current_minute - window) for delta calculation."""
        target = current_minute - self.window_minutes
        if target <= 0:
            return None

        best = None
        best_diff = float('inf')
        for snap in snapshots[:-1]:  # exclude current
            diff = abs(snap.minute - target)
            if diff < best_diff:
                best_diff = diff
                best = snap

        # Only use if within 5 minutes of target
        if best and best_diff <= 5:
            return best
        return None

    def get_signals(self, fixture_id: int) -> PressureSignals | None:
        """Calculate pressure signals for a specific fixture."""
        snaps = self.snapshots.get(fixture_id)
        if not snaps:
            return None

        latest = snaps[-1]
        info = self.fixture_info.get(fixture_id, {})

        if latest.minute < MIN_MINUTE_FOR_SIGNALS:
            return None

        signals = PressureSignals(
            fixture_id=fixture_id,
            home=info.get('home', '?'),
            away=info.get('away', '?'),
            league=info.get('league'),
            stats_minute=latest.stats_minute,
            minute=latest.minute,
            score=f'{latest.home_goals}-{latest.away_goals}',
            home_xg_total=latest.home_xg,
            away_xg_total=latest.away_xg,
            home_possession=latest.home_possession,
            away_possession=latest.away_possession,
            home_reds=latest.home_reds,
            away_reds=latest.away_reds,
            home_shots_on_total=latest.home_shots_on,
            away_shots_on_total=latest.away_shots_on,
            home_shots_total=latest.home_shots_total,
            away_shots_total=latest.away_shots_total,
            home_shots_inside_total=latest.home_shots_inside,
            away_shots_inside_total=latest.away_shots_inside,
            home_corners_total=latest.home_corners,
            away_corners_total=latest.away_corners,
            home_goals=latest.home_goals,
            away_goals=latest.away_goals,
        )

        # Any non-zero stat proves the competition is covered. Goals alone do
        # not: they come from the fixtures feed, which covers everything.
        signals.has_stats = any((
            latest.home_shots_total, latest.away_shots_total,
            latest.home_shots_on, latest.away_shots_on,
            latest.home_corners, latest.away_corners,
            latest.home_xg, latest.away_xg,
        ))

        # Window deltas
        window_start = self._find_window_start(snaps, latest.minute)
        # A baseline carrying the same fetch as `latest` is not a baseline. Every
        # counter differences to 0, which reads as a dead match rather than as an
        # unmeasured one — and the scaled-totals fallback must not step in either,
        # because scaling a frozen stat block reports steady play as a surge. So
        # take neither path: leave the deltas at zero and mark the row.
        signals.stats_frozen = bool(
            window_start is not None
            and latest.stats_fetched_at is not None
            and window_start.stats_fetched_at == latest.stats_fetched_at
        )
        signals.has_window = window_start is not None and not signals.stats_frozen
        if signals.has_window:
            signals.home_shots_on_window = max(0, latest.home_shots_on - window_start.home_shots_on)
            signals.away_shots_on_window = max(0, latest.away_shots_on - window_start.away_shots_on)
            signals.home_shots_inside_window = max(0, latest.home_shots_inside - window_start.home_shots_inside)
            signals.away_shots_inside_window = max(0, latest.away_shots_inside - window_start.away_shots_inside)
            signals.home_xg_window = max(0.0, latest.home_xg - window_start.home_xg)
            signals.away_xg_window = max(0.0, latest.away_xg - window_start.away_xg)
            signals.home_corners_window = max(0, latest.home_corners - window_start.home_corners)
            signals.away_corners_window = max(0, latest.away_corners - window_start.away_corners)
        elif not signals.stats_frozen:
            # No window baseline — use full-match stats scaled to window
            if latest.minute > 0:
                scale = min(1.0, self.window_minutes / latest.minute)
                signals.home_shots_on_window = round(latest.home_shots_on * scale)
                signals.away_shots_on_window = round(latest.away_shots_on * scale)
                signals.home_xg_window = latest.home_xg * scale
                signals.away_xg_window = latest.away_xg * scale

        # xG overperformance: goals minus expected goals
        signals.xg_overperformance_home = latest.home_goals - latest.home_xg
        signals.xg_overperformance_away = latest.away_goals - latest.away_xg

        # Pressure without goals: high xG in window but no goals scored in window
        goals_in_window_h = latest.home_goals - (window_start.home_goals if window_start else 0)
        goals_in_window_a = latest.away_goals - (window_start.away_goals if window_start else 0)
        signals.pressure_without_goals_home = (signals.home_xg_window >= 0.4 and goals_in_window_h == 0)
        signals.pressure_without_goals_away = (signals.away_xg_window >= 0.4 and goals_in_window_a == 0)

        # Shot dominance ratio in window
        total_shots_window = (signals.home_shots_on_window + signals.away_shots_on_window)
        if total_shots_window > 0:
            signals.shot_dominance_home = signals.home_shots_on_window / total_shots_window
            signals.shot_dominance_away = signals.away_shots_on_window / total_shots_window

        # Composite danger index (0–100). xG coverage is a property of the FEED
        # for this fixture, so it is read off the TOTALS and across both sides:
        # a window with no xG in it is a quiet ten minutes, which is exactly what
        # the index should score low — a competition api-football publishes no xG
        # for is not a measurement at all.
        has_xg = bool(latest.home_xg or latest.away_xg)
        signals.home_danger_index = self._danger_index(
            signals.home_shots_on_window, signals.home_shots_inside_window,
            signals.home_xg_window, signals.home_corners_window,
            signals.home_possession, latest.minute, has_xg=has_xg)
        signals.away_danger_index = self._danger_index(
            signals.away_shots_on_window, signals.away_shots_inside_window,
            signals.away_xg_window, signals.away_corners_window,
            signals.away_possession, latest.minute, has_xg=has_xg)

        return signals

    def _danger_index(self, shots_on: int, shots_inside: int,
                      xg: float, corners: int, possession: float,
                      minute: int, has_xg: bool = True) -> float:
        """Composite danger score 0–100 over the rolling window.

        This used to drop has_xg on purpose, to protect the record Live Pressure
        Overs had already accumulated against the un-renormalised score. The
        price of that turned out to be the arm itself: measured on tradeable
        rows at minute 75+ over the seven days to 2026-08-26, fixtures whose feed
        carries no xG peaked at **29.4** against a MIN_PRESSURE of 45 — 0 of 372
        rows could ever have entered, excluding 32 of 53 fixtures (60%) by
        construction rather than because they were played quietly. The gate was
        therefore not "pressure >= 45" but "has xG AND pressure >= 45", which is
        not the hypothesis registered in db/031.

        Renormalised from obs_version 3 (2026-08-26). v2 and v3 rows are two
        different measurements and must never be pooled into one yield; the
        agent's --report already splits on obs_version, and has_xg is on every
        row so the fit can control for it.
        """
        return danger_index(shots_on, shots_inside, xg, corners, possession,
                            has_xg=has_xg)

    def get_all_signals(self) -> dict[int, PressureSignals]:
        """Get pressure signals for all tracked fixtures."""
        result = {}
        for fid in self.snapshots:
            sig = self.get_signals(fid)
            if sig:
                result[fid] = sig
        return result

    def get_snapshot_count(self, fixture_id: int) -> int:
        return len(self.snapshots.get(fixture_id, []))


# ─── Strategy signals for the Poisson trader ────────────────────────────────

@dataclass
class LiveEdgeSignal:
    """A pressure-based edge signal to feed into poisson_trader."""
    fixture_id: int
    home: str
    away: str
    minute: int
    score: str
    signal_type: str           # e.g. 'pressure_no_goal', 'xg_regression', 'late_push'
    direction: str             # 'over', 'home', 'away', 'draw'
    confidence: float          # 0–1
    reasoning: str
    lambda_boost_home: float = 1.0   # multiplier to apply to home lambda
    lambda_boost_away: float = 1.0


def derive_edge_signals(signals: PressureSignals) -> list[LiveEdgeSignal]:
    """
    Analyze pressure signals and produce actionable edge signals.
    These get fed into the Poisson model as lambda adjustments.
    """
    edges: list[LiveEdgeSignal] = []

    # ── 1. Pressure without goals → next goal / over is underpriced
    # A team creating 0.4+ xG in 15 min without scoring is building pressure.
    # The market tends to underprice goals when a team is dominating but not converting.
    for side in ['home', 'away']:
        pressing = getattr(signals, f'pressure_without_goals_{side}')
        xg_w = getattr(signals, f'{side}_xg_window')
        shots_on = getattr(signals, f'{side}_shots_on_window')
        team = getattr(signals, side)

        if pressing and xg_w >= 0.4 and shots_on >= 2:
            # Scale confidence by how dominant the pressure is
            conf = min(0.9, 0.5 + xg_w * 0.3 + shots_on * 0.05)
            boost = 1.0 + min(0.25, xg_w * 0.2)
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='pressure_no_goal',
                direction='over',
                confidence=conf,
                reasoning=f'{team} creating {xg_w:.2f} xG + {shots_on} shots on target '
                          f'in last {PRESSURE_WINDOW_MIN} min without scoring. '
                          f'Pressure likely to convert → over/goal underpriced.',
                lambda_boost_home=boost if side == 'home' else 1.0,
                lambda_boost_away=boost if side == 'away' else 1.0,
            ))

    # ── 2. xG regression signal → team overperforming will regress
    # If a team has scored much more than their xG, the market overprices them.
    # If they've scored much less, the market underprices them.
    for side in ['home', 'away']:
        overperf = getattr(signals, f'xg_overperformance_{side}')
        xg_total = getattr(signals, f'{side}_xg_total')
        team = getattr(signals, side)

        # Underperforming xG by 1+ goal and still creating chances
        if overperf <= -1.0 and xg_total >= 1.5:
            conf = min(0.8, 0.4 + abs(overperf) * 0.2)
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='xg_underperformance',
                direction=side,
                confidence=conf,
                reasoning=f'{team} has {xg_total:.2f} xG but only '
                          f'{getattr(signals, "score").split("-")[0 if side == "home" else 1]} goals. '
                          f'Underperforming by {abs(overperf):.1f} goals — '
                          f'regression favors {side} result + over.',
                lambda_boost_home=1.10 if side == 'home' else 1.0,
                lambda_boost_away=1.10 if side == 'away' else 1.0,
            ))

        # Overperforming — market likely overprices continuation
        if overperf >= 1.0 and signals.minute >= 60:
            conf = min(0.7, 0.3 + overperf * 0.15)
            other = 'away' if side == 'home' else 'home'
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='xg_overperformance',
                direction=other,
                confidence=conf,
                reasoning=f'{team} overperforming xG by {overperf:+.1f} goals. '
                          f'Lucky — market likely overprices their lead.',
                lambda_boost_home=0.95 if side == 'home' else 1.05,
                lambda_boost_away=0.95 if side == 'away' else 1.05,
            ))

    # ── 3. Shot dominance + late game → goal coming
    # One team has 70%+ shots in the window after minute 65
    for side in ['home', 'away']:
        dominance = getattr(signals, f'shot_dominance_{side}')
        danger = getattr(signals, f'{side}_danger_index')
        team = getattr(signals, side)

        if dominance >= 0.70 and danger >= 50 and signals.minute >= 65:
            conf = min(0.85, 0.4 + dominance * 0.3 + danger * 0.002)
            boost = 1.0 + (dominance - 0.5) * 0.3
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='late_shot_dominance',
                direction=side,
                confidence=conf,
                reasoning=f'{team} dominating shots ({dominance:.0%}) with danger index '
                          f'{danger:.0f}/100 after {signals.minute}\'. '
                          f'Late-game pressure → {side} goal / over underpriced.',
                lambda_boost_home=boost if side == 'home' else 1.0,
                lambda_boost_away=boost if side == 'away' else 1.0,
            ))

    # ── 4. Red card + pressure → amplified edge
    if signals.home_reds > 0 or signals.away_reds > 0:
        # Team with numerical advantage + high danger = strong signal
        if signals.home_reds > 0 and signals.away_danger_index >= 40:
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='red_card_pressure',
                direction='away',
                confidence=min(0.85, 0.5 + signals.away_danger_index * 0.004),
                reasoning=f'{signals.home} down to {11 - signals.home_reds} men, '
                          f'{signals.away} danger index at {signals.away_danger_index:.0f}. '
                          f'Numerical advantage + pressure → away/over underpriced.',
                lambda_boost_home=1.0,
                lambda_boost_away=1.10,
            ))
        if signals.away_reds > 0 and signals.home_danger_index >= 40:
            edges.append(LiveEdgeSignal(
                fixture_id=signals.fixture_id,
                home=signals.home, away=signals.away,
                minute=signals.minute, score=signals.score,
                signal_type='red_card_pressure',
                direction='home',
                confidence=min(0.85, 0.5 + signals.home_danger_index * 0.004),
                reasoning=f'{signals.away} down to {11 - signals.away_reds} men, '
                          f'{signals.home} danger index at {signals.home_danger_index:.0f}. '
                          f'Numerical advantage + pressure → home/over underpriced.',
                lambda_boost_home=1.10,
                lambda_boost_away=1.0,
            ))

    return edges


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    tracker = LiveMatchTracker()
    print('Polling live fixtures...\n')
    signals = tracker.poll()

    if not signals:
        print('No signals (matches may be too early or no stats available).')
    else:
        print(f'\n{"═" * 60}')
        print(f'PRESSURE SIGNALS — {len(signals)} match(es)')
        print(f'{"═" * 60}')
        for fid, sig in sorted(signals.items(), key=lambda x: -x[1].home_danger_index - x[1].away_danger_index):
            print(f'\n{sig.summary()}')
            edge_signals = derive_edge_signals(sig)
            if edge_signals:
                for es in edge_signals:
                    print(f'  🎯 [{es.signal_type}] {es.direction} — conf={es.confidence:.0%}')
                    print(f'     {es.reasoning}')
