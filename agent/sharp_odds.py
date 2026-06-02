"""
sharp_odds.py — de-vigged Pinnacle probability for a given match/outcome AT SCAN
TIME, so the edge engine can anchor on the sharp line before betting (not only
post-hoc for CLV).

Thin wrapper over closing_collector's already-tested machinery:
  _find_fixture → fixture id + kickoff
  _fetch_pinnacle_odds → de-vigged probs for all bet types
  _outcome_to_closing_prob → prob for our outcome_key ('home_win', 'over_2_5', ...)

Per-process caches so a whole scan does ~1 fixtures call per date + 1 odds call
per match. Returns None whenever the sharp line is unavailable (minor leagues,
markets api-football's Pinnacle doesn't cover like BTTS) — the engine then falls
back to model-only.
"""

from __future__ import annotations

from typing import Optional

import closing_collector as cc

# Per-process caches (one scan = one process).
_fixtures_by_date: dict[str, list] = {}     # date → fixtures (closing_collector format)
_odds_by_fixture: dict[int, Optional[dict]] = {}  # fixture_id → de-vigged odds dict
_fixture_lookup: dict[tuple, tuple] = {}    # (home,away,date) → (fixture_id, kickoff)


def sharp_prob(home: str, away: str, date_str: Optional[str],
               outcome_key: str) -> Optional[float]:
    """De-vigged Pinnacle prob for `outcome_key`, or None if unavailable."""
    key = (home, away, date_str)
    if key in _fixture_lookup:
        fixture_id, _ = _fixture_lookup[key]
    else:
        fixture_id, kickoff = cc._find_fixture(home, away, date_str, _fixtures_by_date)
        _fixture_lookup[key] = (fixture_id, kickoff)
    if not fixture_id:
        return None

    if fixture_id in _odds_by_fixture:
        odds = _odds_by_fixture[fixture_id]
    else:
        odds = cc._fetch_pinnacle_odds(fixture_id)
        if odds:
            odds["home"], odds["away"] = home, away
        _odds_by_fixture[fixture_id] = odds
    if not odds:
        return None

    return cc._outcome_to_closing_prob(outcome_key, odds)


def reset_cache() -> None:
    _fixtures_by_date.clear()
    _odds_by_fixture.clear()
    _fixture_lookup.clear()
