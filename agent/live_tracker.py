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
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

log = logging.getLogger(__name__)

PRESSURE_WINDOW_MIN = 15
MIN_MINUTE_FOR_SIGNALS = 15


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


@dataclass
class PressureSignals:
    fixture_id: int
    home: str
    away: str
    minute: int
    score: str
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


class LiveMatchTracker:
    def __init__(self, window_minutes: int = PRESSURE_WINDOW_MIN):
        self.api_key = os.getenv('FOOTBALL_API_KEY', '')
        self.window_minutes = window_minutes
        self.snapshots: dict[int, list[StatSnapshot]] = defaultdict(list)
        self.fixture_info: dict[int, dict] = {}
        self._last_poll: float = 0

    def poll(self) -> dict[int, PressureSignals]:
        """
        Fetch all live fixtures, store stat snapshots, return pressure signals.
        """
        if not self.api_key:
            log.warning('[tracker] No FOOTBALL_API_KEY set')
            return {}

        try:
            # Fetch live fixtures
            resp = requests.get(
                'https://v3.football.api-sports.io/fixtures',
                params={'live': 'all'},
                headers={'x-apisports-key': self.api_key},
                timeout=10,
            )
            if resp.status_code != 200:
                log.warning(f'[tracker] api-football HTTP {resp.status_code}')
                return {}

            fixtures = resp.json().get('response', [])
            log.info(f'[tracker] {len(fixtures)} live fixtures')

            fixture_ids_with_stats = []

            for f in fixtures:
                fid = f['fixture']['id']
                home = f['teams']['home']['name']
                away = f['teams']['away']['name']
                elapsed = f['fixture']['status'].get('elapsed', 0)
                goals_h = f['goals'].get('home', 0) or 0
                goals_a = f['goals'].get('away', 0) or 0
                league = f.get('league', {}).get('name', '?')

                self.fixture_info[fid] = {
                    'home': home, 'away': away,
                    'league': league,
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

                self.snapshots[fid].append(snap)
                fixture_ids_with_stats.append(fid)

            # Fetch detailed stats for fixtures that need them
            # (batch by fixture ID — the inline stats may be incomplete)
            enriched = 0
            for fid in fixture_ids_with_stats:
                snaps = self.snapshots[fid]
                latest = snaps[-1]
                # Only fetch detailed stats if inline stats were empty
                if latest.home_shots_total == 0 and latest.away_shots_total == 0 and latest.minute > 5:
                    if self._enrich_fixture(fid, latest):
                        enriched += 1

            if enriched:
                log.info(f'[tracker] Enriched {enriched} fixtures with detailed stats')

            self._last_poll = time.time()

            # Generate signals for all tracked matches
            return self.get_all_signals()

        except Exception as e:
            log.error(f'[tracker] Poll error: {e}')
            return {}

    def _enrich_fixture(self, fid: int, snap: StatSnapshot) -> bool:
        """Fetch detailed statistics for a specific fixture."""
        try:
            resp = requests.get(
                'https://v3.football.api-sports.io/fixtures/statistics',
                params={'fixture': fid},
                headers={'x-apisports-key': self.api_key},
                timeout=8,
            )
            if resp.status_code != 200:
                return False

            stats = resp.json().get('response', [])
            if not stats:
                return False

            info = self.fixture_info.get(fid, {})
            home = info.get('home', '')

            for team_stats in stats:
                is_home = team_stats.get('team', {}).get('name') == home
                self._parse_stats(snap, team_stats.get('statistics', []), is_home)

            return True
        except Exception:
            return False

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
            minute=latest.minute,
            score=f'{latest.home_goals}-{latest.away_goals}',
            home_xg_total=latest.home_xg,
            away_xg_total=latest.away_xg,
            home_possession=latest.home_possession,
            away_possession=latest.away_possession,
            home_reds=latest.home_reds,
            away_reds=latest.away_reds,
        )

        # Window deltas
        window_start = self._find_window_start(snaps, latest.minute)
        if window_start:
            signals.home_shots_on_window = max(0, latest.home_shots_on - window_start.home_shots_on)
            signals.away_shots_on_window = max(0, latest.away_shots_on - window_start.away_shots_on)
            signals.home_shots_inside_window = max(0, latest.home_shots_inside - window_start.home_shots_inside)
            signals.away_shots_inside_window = max(0, latest.away_shots_inside - window_start.away_shots_inside)
            signals.home_xg_window = max(0.0, latest.home_xg - window_start.home_xg)
            signals.away_xg_window = max(0.0, latest.away_xg - window_start.away_xg)
            signals.home_corners_window = max(0, latest.home_corners - window_start.home_corners)
            signals.away_corners_window = max(0, latest.away_corners - window_start.away_corners)
        else:
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

        # Composite danger index (0–100)
        signals.home_danger_index = self._danger_index(
            signals.home_shots_on_window, signals.home_shots_inside_window,
            signals.home_xg_window, signals.home_corners_window,
            signals.home_possession, latest.minute)
        signals.away_danger_index = self._danger_index(
            signals.away_shots_on_window, signals.away_shots_inside_window,
            signals.away_xg_window, signals.away_corners_window,
            signals.away_possession, latest.minute)

        return signals

    def _danger_index(self, shots_on: int, shots_inside: int,
                      xg: float, corners: int, possession: float,
                      minute: int) -> float:
        """
        Composite danger score 0–100.
        Weights: xG(40%) + shots_on(25%) + shots_inside(15%) + corners(10%) + possession(10%)
        """
        # Normalize each to 0–100 scale
        xg_score = min(100, xg * 100)                   # 1.0 xG in window = 100
        shots_on_score = min(100, shots_on * 20)         # 5 shots on target = 100
        shots_inside_score = min(100, shots_inside * 15) # ~7 shots inside = 100
        corners_score = min(100, corners * 15)           # ~7 corners = 100
        poss_score = max(0, (possession - 30) / 40 * 100)  # 30%=0, 70%=100

        return (
            xg_score * 0.40 +
            shots_on_score * 0.25 +
            shots_inside_score * 0.15 +
            corners_score * 0.10 +
            poss_score * 0.10
        )

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
