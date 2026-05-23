"""
Live Odds Tracker — fetches real-time odds from api-football.com
and compares against Polymarket + our models.

Bookmakers tracked: Pinnacle, Bet365, Betfair (primary), William Hill, Smarkets

Bet types supported:
  - Match Winner (1x2)
  - Over/Under (2.5, 1.5, 3.5, etc)
  - Handicap (spreads: -1.5, -0.5, +0.5, +1.5)

Line movement detection: tracks Pinnacle odds changes from kickoff onwards.

Usage:
    tracker = LiveOddsTracker()
    consensus = tracker.get_consensus(fixture_id, home, away, bet_type='Match Winner')
    line_move = tracker.get_line_movement(fixture_id, home, away, 'Match Winner')
    # → {'pinnacle_home': +2.5pp, 'pinnacle_away': -1.8pp, ...}
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

# Primary bookmakers for consensus (sharp books)
PRIMARY_BOOKMAKERS = ['Pinnacle', 'Betfair', 'Smarkets']
SECONDARY_BOOKMAKERS = ['Bet365', 'William Hill', 'Bwin']


@dataclass
class OddsSnapshot:
    """Single bookmaker's odds snapshot"""
    bookmaker: str
    home_odds: float = 0.0
    draw_odds: float = 0.0
    away_odds: float = 0.0
    timestamp: float = 0.0

    def implied_probs(self) -> dict[str, float] | None:
        """Convert decimal odds to implied probabilities (vig-removed)."""
        if not (self.home_odds > 0 and self.away_odds > 0):
            return None

        # Handle both 1x2 (with draw) and spreads/O/U (no draw)
        if self.draw_odds > 0:
            # 1x2 format (home/draw/away)
            total_vig = 1 / self.home_odds + 1 / self.draw_odds + 1 / self.away_odds
            if total_vig <= 0:
                return None
            return {
                'home': (1 / self.home_odds) / total_vig,
                'draw': (1 / self.draw_odds) / total_vig,
                'away': (1 / self.away_odds) / total_vig,
            }
        else:
            # Spreads/O/U format (home/away only, no draw)
            total_vig = 1 / self.home_odds + 1 / self.away_odds
            if total_vig <= 0:
                return None
            return {
                'home': (1 / self.home_odds) / total_vig,
                'draw': 0.0,
                'away': (1 / self.away_odds) / total_vig,
            }


@dataclass
class OddsConsensus:
    """Multi-bookmaker consensus"""
    fixture_id: int
    home: str
    away: str
    pinnacle: OddsSnapshot | None = None
    betfair: OddsSnapshot | None = None
    smarkets: OddsSnapshot | None = None
    bet365: OddsSnapshot | None = None
    wh: OddsSnapshot | None = None

    def consensus_probs(self) -> dict[str, float] | None:
        """Average implied probs from primary bookmakers."""
        sharps = [bm for bm in [self.pinnacle, self.betfair, self.smarkets] if bm]
        if not sharps:
            return None

        probs_list = [bm.implied_probs() for bm in sharps]
        probs_list = [p for p in probs_list if p]
        if not probs_list:
            return None

        avg = {
            'home': sum(p['home'] for p in probs_list) / len(probs_list),
            'draw': sum(p['draw'] for p in probs_list) / len(probs_list),
            'away': sum(p['away'] for p in probs_list) / len(probs_list),
        }
        return avg

    def edge_vs_pm(self, outcome: str, pm_price: float) -> dict[str, Any]:
        """Calculate edge between PM yes_price and bookmaker consensus."""
        consensus = self.consensus_probs()
        if not consensus:
            return {}

        fair_prob = consensus.get(outcome, 0)
        if fair_prob <= 0:
            return {}

        edge_pp = (fair_prob - pm_price) * 100
        return {
            'fair_prob': fair_prob,
            'pm_price': pm_price,
            'edge_pp': edge_pp,
            'confidence': 'high' if len([bm for bm in [self.pinnacle, self.betfair, self.smarkets] if bm]) >= 2 else 'medium',
        }


class LiveOddsTracker:
    """Fetch and track live odds from api-football.com"""

    def __init__(self):
        self.api_key = os.getenv('FOOTBALL_API_KEY', '')
        self.cache: dict[str, OddsConsensus] = {}
        self.fixture_cache: dict[tuple[str, str], int] = {}  # (home, away) → fixture_id
        # Historical snapshots for line movement detection: (fixture_id, bet_type) → [OddsConsensus, ...]
        self.history: dict[tuple[int, str], list[OddsConsensus]] = {}

    def _history_key(self, fixture_id: int, bet_type: str) -> tuple[int, str]:
        """Generate history key."""
        return (fixture_id, bet_type)

    def get_fixture_id(self, home: str, away: str) -> int | None:
        """Find api-football fixture_id for a match (home/away team names)."""
        cache_key = (home.lower(), away.lower())
        if cache_key in self.fixture_cache:
            return self.fixture_cache[cache_key]

        if not self.api_key:
            return None

        try:
            # Search for live fixtures matching the teams
            resp = requests.get(
                'https://v3.football.api-sports.io/fixtures',
                params={'live': 'all'},
                headers={'x-apisports-key': self.api_key},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            fixtures = resp.json().get('response', [])
            for f in fixtures:
                h_team = f.get('teams', {}).get('home', {}).get('name', '').lower()
                a_team = f.get('teams', {}).get('away', {}).get('name', '').lower()
                if (h_team == home.lower() and a_team == away.lower()):
                    fid = f.get('fixture', {}).get('id')
                    if fid:
                        self.fixture_cache[cache_key] = fid
                        return fid

            return None
        except Exception:
            return None

    def get_consensus(
        self, fixture_id: int, home: str, away: str, bet_type: str = 'Match Winner'
    ) -> OddsConsensus | None:
        """
        Fetch live odds consensus for a fixture.
        Supported bet_type: 'Match Winner', 'Over 2.5', 'Over 1.5', 'Handicap', etc.
        """
        if not self.api_key:
            log.warning('[live_odds] No FOOTBALL_API_KEY set')
            return None

        try:
            resp = requests.get(
                'https://v3.football.api-sports.io/odds',
                params={'fixture': fixture_id, 'bet': bet_type},
                headers={'x-apisports-key': self.api_key},
                timeout=10,
            )
            if resp.status_code != 200:
                log.debug(f'[live_odds] API HTTP {resp.status_code}')
                return None

            data = resp.json().get('response', [])
            if not data:
                return None

            consensus = OddsConsensus(fixture_id=fixture_id, home=home, away=away)

            for item in data:
                bookmakers = item.get('bookmakers', [])
                for bm in bookmakers:
                    name = bm.get('name', '').lower()
                    bets = bm.get('bets', [])
                    if not bets:
                        continue

                    values = bets[0].get('values', [])

                    # Parse odds based on bet type
                    home_odds = away_odds = draw_odds = 0.0

                    if 'Match Winner' in bet_type or '1x2' in bet_type:
                        # 1x2 format: [home, draw, away]
                        if len(values) >= 3:
                            try:
                                home_odds = float(values[0].get('odd', 0))
                                draw_odds = float(values[1].get('odd', 0))
                                away_odds = float(values[2].get('odd', 0))
                            except (ValueError, TypeError):
                                continue
                    elif 'Over' in bet_type or 'Under' in bet_type:
                        # Over/Under format: [Over, Under]
                        if len(values) >= 2:
                            try:
                                home_odds = float(values[0].get('odd', 0))  # Over
                                away_odds = float(values[1].get('odd', 0))  # Under
                            except (ValueError, TypeError):
                                continue
                    elif 'Handicap' in bet_type:
                        # Handicap format: [Home spread, Away spread]
                        if len(values) >= 2:
                            try:
                                home_odds = float(values[0].get('odd', 0))
                                away_odds = float(values[1].get('odd', 0))
                            except (ValueError, TypeError):
                                continue

                    if home_odds <= 0 or away_odds <= 0:
                        continue

                    snap = OddsSnapshot(
                        bookmaker=bm.get('name', '?'),
                        home_odds=home_odds,
                        draw_odds=draw_odds,
                        away_odds=away_odds,
                    )

                    if 'pinnacle' in name:
                        consensus.pinnacle = snap
                    elif 'betfair' in name:
                        consensus.betfair = snap
                    elif 'smarkets' in name:
                        consensus.smarkets = snap
                    elif 'bet365' in name:
                        consensus.bet365 = snap
                    elif 'william' in name or 'hill' in name:
                        consensus.wh = snap

            if consensus.pinnacle or consensus.betfair:
                # Store in history for line movement tracking
                hist_key = self._history_key(fixture_id, bet_type)
                if hist_key not in self.history:
                    self.history[hist_key] = []
                self.history[hist_key].append(consensus)
                return consensus

            return None

        except Exception as e:
            log.debug(f'[live_odds] Error: {e}')
            return None

    def get_line_movement(
        self, fixture_id: int, home: str, away: str, bet_type: str = 'Match Winner'
    ) -> dict[str, Any]:
        """
        Detect line movement from kickoff to now.
        Compares current odds vs. first snapshot (at kickoff).
        Returns movements > 2pp for each outcome.
        """
        hist_key = self._history_key(fixture_id, bet_type)
        snapshots = self.history.get(hist_key, [])

        if len(snapshots) < 2:
            return {}

        # Compare first (kickoff) vs last (current)
        kickoff = snapshots[0]
        current = snapshots[-1]
        movement = {}

        # Pinnacle line movement (primary smart money indicator)
        if current.pinnacle and kickoff.pinnacle:
            curr_p = current.pinnacle.implied_probs()
            kick_p = kickoff.pinnacle.implied_probs()
            if curr_p and kick_p:
                for outcome in ['home', 'draw', 'away']:
                    move_pp = (curr_p[outcome] - kick_p[outcome]) * 100
                    if abs(move_pp) >= 2.0:  # threshold: 2pp
                        movement[f'pinnacle_{outcome}'] = move_pp
                        direction = '📈' if move_pp > 0 else '📉'
                        log.debug(f'[line_movement] Pinnacle {outcome} {direction} {move_pp:+.1f}pp')

        # Betfair line movement (confirmation)
        if current.betfair and kickoff.betfair:
            curr_p = current.betfair.implied_probs()
            kick_p = kickoff.betfair.implied_probs()
            if curr_p and kick_p:
                for outcome in ['home', 'draw', 'away']:
                    move_pp = (curr_p[outcome] - kick_p[outcome]) * 100
                    if abs(move_pp) >= 2.0:
                        movement[f'betfair_{outcome}'] = move_pp

        return movement

    def get_consensus_history(
        self, fixture_id: int, bet_type: str = 'Match Winner'
    ) -> list[OddsConsensus]:
        """Get all stored consensus snapshots for a fixture/bet_type pair."""
        hist_key = self._history_key(fixture_id, bet_type)
        return self.history.get(hist_key, [])


def get_pm_vs_bookmaker_edge(
    pm_price: float, consensus: OddsConsensus, outcome: str
) -> dict[str, Any]:
    """
    Compare PM yes_price against bookmaker consensus.
    Returns edge in pp and confidence level.
    """
    if not consensus:
        return {}

    edge_data = consensus.edge_vs_pm(outcome, pm_price)
    if not edge_data:
        return {}

    # Add bookmaker breakdown for logging
    bookmakers_used = []
    if consensus.pinnacle:
        bookmakers_used.append('Pinnacle')
    if consensus.betfair:
        bookmakers_used.append('Betfair')
    if consensus.smarkets:
        bookmakers_used.append('Smarkets')

    edge_data['bookmakers_used'] = bookmakers_used
    return edge_data


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    tracker = LiveOddsTracker()
    # Test with a fixture ID (you'd need a real live fixture)
    consensus = tracker.get_consensus(1045200, 'Arsenal', 'Chelsea')
    if consensus:
        print(f'Pinnacle: {consensus.pinnacle}')
        print(f'Betfair: {consensus.betfair}')
        print(f'Consensus probs: {consensus.consensus_probs()}')
