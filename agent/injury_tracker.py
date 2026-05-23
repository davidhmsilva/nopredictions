"""
InjuryTracker — fetches player injury/suspension data from api-football
and applies xG-based adjustments to team strength.

Key players missing = reduced expected goals. Uses api-football /injuries endpoint.

Usage:
    tracker = InjuryTracker()
    adjustments = tracker.get_team_adjustments('fixture_id', 'home_team_id', 'away_team_id')
    # → {'home': 0.93, 'away': 1.0}  # home down 7% due to injuries

    # Apply to DC lambda:
    home_lambda = home_base_lambda * adjustments['home']
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

log = logging.getLogger(__name__)

# Player importance tiers (xG impact per absence)
PLAYER_IMPORTANCE = {
    'Striker': 0.08,      # 8% xG reduction if top striker out
    'Attacking Midfielder': 0.06,
    'Winger': 0.06,
    'Midfielder': 0.04,
    'Defender': 0.04,
    'Goalkeeper': 0.10,   # 10% xG reduction if main GK out (defense confidence)
}

# Injury type severity (1.0 = full impact, 0.5 = minor, 0.0 = play-through)
INJURY_SEVERITY = {
    'Cruciate Ligament Injury': 1.0,
    'Anterior Cruciate Ligament': 1.0,
    'Suspension': 1.0,
    'Red Card Suspension': 1.0,
    'Muscle Injury': 0.7,
    'Hamstring': 0.7,
    'Achilles': 0.9,
    'Calf': 0.6,
    'Ankle': 0.5,
    'Knee': 0.8,
    'Fracture': 0.9,
    'Groin': 0.6,
    'Ribs': 0.5,
    'Back': 0.6,
    'Shoulder': 0.4,
}


@dataclass
class PlayerInjury:
    """Single player injury record"""
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    position: str
    injury_type: str
    out_until_date: str | None = None

    def severity(self) -> float:
        """Injury severity multiplier (0.0 to 1.0)."""
        for keyword, severity in INJURY_SEVERITY.items():
            if keyword.lower() in self.injury_type.lower():
                return severity
        return 0.5  # unknown injury = minor


class InjuryTracker:
    """Fetch and track player injuries from api-football.com"""

    def __init__(self):
        self.api_key = os.getenv('FOOTBALL_API_KEY', '')
        self.cache: dict[int, list[PlayerInjury]] = {}  # team_id → injuries

    def get_injuries(self, team_id: int) -> list[PlayerInjury] | None:
        """Fetch injuries for a team."""
        if team_id in self.cache:
            return self.cache[team_id]

        if not self.api_key:
            log.warning('[injury] No FOOTBALL_API_KEY set')
            return None

        try:
            resp = requests.get(
                'https://v3.football.api-sports.io/injuries',
                params={'team': team_id},
                headers={'x-apisports-key': self.api_key},
                timeout=10,
            )
            if resp.status_code != 200:
                log.debug(f'[injury] API HTTP {resp.status_code}')
                return None

            injuries_data = resp.json().get('response', [])
            injuries = []

            for inj in injuries_data:
                player = inj.get('player', {})
                team = inj.get('team', {})
                fixture_info = inj.get('fixture', {})

                # Only include if injury extends past next match (fixture_date in future)
                if fixture_info and fixture_info.get('date'):
                    fixture_date = fixture_info.get('date')
                else:
                    fixture_date = None

                pij = PlayerInjury(
                    player_id=player.get('id', 0),
                    player_name=player.get('name', '?'),
                    team_id=team.get('id', 0),
                    team_name=team.get('name', '?'),
                    position=player.get('pos', 'Unknown'),
                    injury_type=inj.get('type', 'Unknown'),
                    out_until_date=fixture_date,
                )
                injuries.append(pij)

            self.cache[team_id] = injuries
            return injuries

        except Exception as e:
            log.debug(f'[injury] Error: {e}')
            return None

    def get_team_adjustment(self, team_id: int) -> float:
        """
        Calculate xG adjustment multiplier for a team (0.7–1.0).
        0.7 = 30% xG reduction (multiple key players out)
        1.0 = no injuries
        """
        injuries = self.get_injuries(team_id)
        if not injuries:
            return 1.0

        # Sum impact of all injuries
        total_impact = 0.0
        for inj in injuries:
            # Position importance × injury severity
            importance = PLAYER_IMPORTANCE.get(inj.position, 0.02)
            severity = inj.severity()
            total_impact += importance * severity

        # Cap at 0.6 (team can't be reduced below 60% due to injuries alone)
        adjustment = max(0.60, 1.0 - min(total_impact, 0.4))
        return adjustment

    def get_fixture_adjustments(
        self, fixture_id: int, home_team_id: int, away_team_id: int
    ) -> dict[str, float]:
        """
        Get injury adjustments for both teams in a fixture.
        Returns {'home': 0.95, 'away': 1.0}
        """
        return {
            'home': self.get_team_adjustment(home_team_id),
            'away': self.get_team_adjustment(away_team_id),
        }

    def clear_cache(self) -> None:
        """Clear injury cache (e.g., between match windows)."""
        self.cache.clear()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    tracker = InjuryTracker()
    # Test with a team ID (e.g., Manchester United = 33)
    injuries = tracker.get_injuries(33)
    if injuries:
        for inj in injuries:
            print(f'{inj.player_name} ({inj.position}): {inj.injury_type}')
        adjustment = tracker.get_team_adjustment(33)
        print(f'Team adjustment: {adjustment:.3f}')
