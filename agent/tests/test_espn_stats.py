"""ESPN adapter — the parsing that can go wrong silently.

Nothing here touches the network. The failure modes worth a test are the ones
that produce a plausible reading rather than an error: a side swapped, a minute
that ignores stoppage time, a not-yet-started match reported as goalless play.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import espn_stats as espn                                    # noqa: E402
from live_tracker import danger_index                        # noqa: E402


def _competitor(side, name, score, sot=0, shots=0, corners=0, poss=None):
    stats = [
        {"name": "shotsOnTarget", "displayValue": str(sot)},
        {"name": "totalShots", "displayValue": str(shots)},
        {"name": "wonCorners", "displayValue": str(corners)},
        {"name": "foulsCommitted", "displayValue": "0"},
    ]
    if poss is not None:
        stats.append({"name": "possessionPct", "displayValue": str(poss)})
    return {"homeAway": side, "score": str(score),
            "team": {"displayName": name}, "statistics": stats}


def _event(competitors, clock="67'", state="in", detail="67'"):
    return {"id": "1", "competitions": [{
        "competitors": competitors,
        "status": {"displayClock": clock, "type": {"state": state, "detail": detail}},
    }]}


def _payload(*events):
    return {"events": list(events)}


class _Res:
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


class _Session:
    def __init__(self, data): self._d = data
    def get(self, *_a, **_k): return _Res(self._d)


# ── sides ────────────────────────────────────────────────────────────────────

def test_the_home_side_is_read_from_the_flag_not_the_order():
    """ESPN usually lists [home, away] and publishes homeAway on each. Trusting
    the order silently inverts possession and shots onto the wrong team the day
    it lists them the other way round — and a pressure reading on the wrong side
    is not a blunted signal, it is a different match."""
    away_first = _event([
        _competitor("away", "Sevilla", 0, sot=3, corners=3, poss=46),
        _competitor("home", "Espanyol", 0, sot=1, corners=0, poss=54),
    ])
    fx = espn.fetch_league("esp.1", _Session(_payload(away_first)))[0]
    assert fx.home == "Espanyol"
    assert fx.away == "Sevilla"
    assert fx.home_stats.shots_on == 1
    assert fx.away_stats.shots_on == 3
    assert fx.home_stats.possession == 54.0


# ── the clock ────────────────────────────────────────────────────────────────

def test_stoppage_time_is_folded_into_the_minute():
    """Every table on this project is keyed on a minute, and reporting 90 for
    both the 90th and the 95th is a lie they cannot see through."""
    fx = espn.fetch_league("x", _Session(_payload(
        _event([_competitor("home", "A", 0), _competitor("away", "B", 0)], clock="90'+5'")
    )))[0]
    assert fx.minute == 95


def test_a_plain_minute_is_read_as_itself():
    fx = espn.fetch_league("x", _Session(_payload(
        _event([_competitor("home", "A", 0), _competitor("away", "B", 0)], clock="67'")
    )))[0]
    assert fx.minute == 67


def test_no_clock_is_none_rather_than_zero():
    """A missing clock must not read as minute 0, which is a real state the
    fair-value tables would happily price."""
    fx = espn.fetch_league("x", _Session(_payload(
        _event([_competitor("home", "A", 0), _competitor("away", "B", 0)],
               clock="", state="pre", detail="Sat, 8:00 PM")
    )))[0]
    assert fx.minute is None
    assert not fx.live


# ── has_stats ────────────────────────────────────────────────────────────────

def test_a_match_that_has_not_started_reports_no_stats():
    """All-zero counters are what a pre-match block looks like AND what a
    genuinely quiet opening two minutes looks like. Possession separates them:
    ESPN does not publish it until play begins."""
    fx = espn.fetch_league("x", _Session(_payload(
        _event([_competitor("home", "A", 0), _competitor("away", "B", 0)],
               state="pre", clock="")
    )))[0]
    assert not fx.has_stats


def test_a_goalless_opening_with_possession_does_report_stats():
    fx = espn.fetch_league("x", _Session(_payload(
        _event([_competitor("home", "A", 0, poss=58),
                _competitor("away", "B", 0, poss=42)])
    )))[0]
    assert fx.has_stats


# ── fixture matching ─────────────────────────────────────────────────────────

def _fx(home, away):
    return espn.EspnFixture(event_id="1", league_code="x", league="X",
                            home=home, away=away, minute=20, state="in", detail="20'")


def test_a_pm_pair_finds_its_espn_fixture():
    got = espn.match_fixture("Sevilla FC", "RCD Espanyol de Barcelona",
                             [_fx("Getafe", "Girona"), _fx("Sevilla", "Espanyol")])
    assert got is not None and got.home == "Sevilla"


def test_a_tie_returns_nothing():
    """Two fixtures scoring identically means the names cannot separate them.
    Picking either is the bug this project already paid for once, when a
    first-word match paired River Plate with Platense."""
    same = [_fx("Sevilla", "Espanyol"), _fx("Sevilla", "Espanyol")]
    assert espn.match_fixture("Sevilla FC", "RCD Espanyol", same) is None


def test_no_plausible_fixture_returns_nothing():
    assert espn.match_fixture("Everton", "Manchester United",
                              [_fx("Getafe", "Girona")]) is None


# ── the index without ESPN's missing terms ───────────────────────────────────

def test_the_missing_terms_are_dropped_not_zeroed():
    """ESPN publishes no team xG and no shots-inside-the-box. Passing zeros
    would score a busy match out of 45 and put it below every threshold that was
    calibrated on a full feed — the exact failure that once excluded every
    no-xG fixture from strategy 16. Dropping and rescaling keeps the axis."""
    full = danger_index(3, 4, 0.6, 2, 60.0)
    espn_shape = danger_index(3, 0, 0.0, 2, 60.0, has_xg=False, has_inside=False)
    zeroed = danger_index(3, 0, 0.0, 2, 60.0)

    assert abs(espn_shape - full) < 5          # same axis
    assert zeroed < full - 15                  # what the naive version would say
    assert espn_shape > zeroed


def test_a_dead_match_still_scores_low_rather_than_nothing():
    """Both optional terms dropped removes 55% of the weight, never 100%, so the
    divide-by-zero guard in danger_index is unreachable with these weights — it
    is there for a future caller that drops more. What a genuinely dead match
    scores is the level midfield possession alone earns, rescaled: well under
    every threshold, and not zero, because 50% possession is a real reading."""
    dead = danger_index(0, 0, 0, 0, 50.0, has_xg=False, has_inside=False)
    assert 0 < dead < 15
    # And a busy one on the same shape has to clear it by a distance.
    busy = danger_index(4, 0, 0, 3, 65.0, has_xg=False, has_inside=False)
    assert busy > dead + 40
