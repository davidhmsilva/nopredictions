"""
Tests for the stats-enrichment budget in LiveMatchTracker.

These exist because the old code collapsed every enrichment failure into
`return False`, so a spent api-football quota was indistinguishable from a
competition that genuinely has no stats. The agent then wrote "no api-football
stats coverage" on 36,917 rows in a single day when the real answer was that we
had stopped being allowed to ask. Each test below pins one half of that
distinction, or the budget that stops us hitting the wall in the first place.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_live_tracker_budget.py -q
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_tracker as lt  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        # The refusal path logs the rate-limit headers alongside the error,
        # because api-football contradicts itself there — it refused every
        # endpoint on 2026-09-02 while reporting 74,999 of 75,000 remaining.
        self.headers = headers or {}

    def json(self):
        return self._payload


def _live_payload(n, minute=80, league=None):
    """n live fixtures, none carrying inline statistics."""
    return {"response": [
        {
            "fixture": {"id": 1000 + i, "status": {"elapsed": minute}},
            "teams": {"home": {"name": f"Home{i}"}, "away": {"name": f"Away{i}"}},
            "goals": {"home": 0, "away": 0},
            "league": {"name": league or f"League{i % 4}"},
            "events": [],
        }
        for i in range(n)
    ]}


def _stats_payload(shots=7):
    return {"response": [
        {"team": {"name": "Home0"}, "statistics": [{"type": "Total Shots", "value": shots}]},
        {"team": {"name": "Away0"}, "statistics": [{"type": "Total Shots", "value": 3}]},
    ]}


@pytest.fixture(autouse=True)
def _no_espn(monkeypatch):
    """From 2026-09-06 a refused api-football poll falls back to ESPN. Every
    refusal test in this file would otherwise make a real request to a third
    party — so the fallback is stubbed empty here, and the tests that are ABOUT
    the fallback stub it with content of their own."""
    monkeypatch.setattr(lt.espn_stats, "live_fixtures", lambda *a, **k: [])
    monkeypatch.setattr(lt.espn_stats, "fetch_league", lambda *a, **k: [])


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("FOOTBALL_API_KEY", "test-key")
    t = lt.LiveMatchTracker()
    t.api_key = "test-key"
    return t


def _install(monkeypatch, live_n, stats_response, calls, league=None):
    def fake_get(url, **kw):
        if "statistics" in url:
            calls.append(kw["params"]["fixture"])
            return stats_response() if callable(stats_response) else stats_response
        return FakeResp(_live_payload(live_n, league=league))
    monkeypatch.setattr(lt.requests, "get", fake_get)


def test_budget_caps_calls_per_cycle(tracker, monkeypatch):
    """90 eligible fixtures must not cost 90 calls — that is the 130k/day burn."""
    calls = []
    _install(monkeypatch, 90, FakeResp(_stats_payload()), calls)
    tracker.poll()
    assert len(calls) == lt.ENRICH_BUDGET_PER_CYCLE
    assert tracker.last_enrich_report.get("over budget") == 90 - lt.ENRICH_BUDGET_PER_CYCLE


def test_priority_order_is_honoured(tracker, monkeypatch):
    """The caller's ranking decides who gets the scarce calls.

    The fixture count is derived from the budget rather than hard-coded: the
    property under test is "the top N by rank win", and it stops being tested at
    all the moment the budget is raised past the number of fixtures."""
    live_n = lt.ENRICH_BUDGET_PER_CYCLE + 5
    calls = []
    _install(monkeypatch, live_n, FakeResp(_stats_payload()), calls)
    # rank purely by fixture id so the expected winners are unambiguous
    tracker.poll(priority=lambda fid, snap: fid)
    assert calls == sorted(calls, reverse=True)
    assert len(calls) == lt.ENRICH_BUDGET_PER_CYCLE
    assert min(calls) == 1000 + live_n - lt.ENRICH_BUDGET_PER_CYCLE


def test_negative_priority_is_never_called(tracker, monkeypatch):
    """A fixture PM does not list can never be traded, so it costs nothing."""
    calls = []
    _install(monkeypatch, 20, FakeResp(_stats_payload()), calls)
    tracker.poll(priority=lambda fid, snap: -1)
    assert calls == []
    assert tracker.last_enrich_report.get("deprioritised") == 20


def test_a_caller_supplied_reason_survives_the_default(tracker, monkeypatch):
    """"Past the last minute we could act on" and "inside the window but sampled
    out to protect the evening allowance" are opposite facts about the same
    missing row. Only the caller knows which, so the tracker must not overwrite
    it with the generic label — that collapse is the mistake this file has
    already paid for three times."""
    calls = []
    _install(monkeypatch, 3, FakeResp(_stats_payload()), calls)

    def priority(fid, snap):
        tracker.enrich_status[fid] = "research sampled out (daytime budget)"
        return -1

    tracker.poll(priority=priority)
    assert calls == []
    assert tracker.last_enrich_report.get("research sampled out (daytime budget)") == 3
    assert "deprioritised" not in tracker.last_enrich_report
    assert all(v == "research sampled out (daytime budget)"
               for v in tracker.enrich_status.values())


def test_ttl_prevents_refetch(tracker, monkeypatch):
    """Stats do not move fast enough to justify a call every 60s."""
    calls = []
    _install(monkeypatch, 5, FakeResp(_stats_payload()), calls)
    tracker.poll()
    first = len(calls)
    tracker.snapshots.clear()          # force fresh snapshots, same fixture ids
    tracker.poll()
    assert len(calls) == first, "second poll inside the TTL must not re-fetch"
    assert tracker.last_enrich_report.get("within TTL") == 5


def test_quota_error_is_not_reported_as_no_coverage(tracker, monkeypatch):
    """The bug this whole module exists for: 200 + errors != 'no stats'."""
    calls = []
    quota = FakeResp({"errors": {"requests": "You have reached the request "
                                             "limit for the day"}, "response": []})
    _install(monkeypatch, 10, quota, calls)
    tracker.poll()
    statuses = set(tracker.enrich_status.values())
    assert lt.DAILY_EXHAUSTED in statuses
    assert "empty" not in statuses
    # and it must stop asking once told the day is spent
    assert len(calls) == 1


def test_per_minute_limit_is_not_reported_as_a_spent_day(tracker, monkeypatch):
    """The two refusals used to share one label, and they mean opposite things.

    On 2026-08-20 the daily allowance was 3% used (2,147 of 75,000) while 176
    recorded rows claimed it was spent — so the label was pointing at the wrong
    constraint, and the backoff it implies is an hour instead of a minute.
    """
    calls = []
    burst = FakeResp({"errors": {"rateLimit": "Too many requests"}, "response": []})
    _install(monkeypatch, 10, burst, calls)
    tracker.poll()

    statuses = set(tracker.enrich_status.values())
    assert lt.RATE_LIMITED in statuses
    assert lt.DAILY_EXHAUSTED not in statuses
    # A per-minute blip must not silence the tracker for an hour.
    assert tracker._quota_spent_until - time.time() <= 120


def test_the_latch_labels_rows_with_the_reason_that_armed_it(tracker, monkeypatch):
    """Rows skipped by the latch are labelled from an event that happened to a
    DIFFERENT fixture — no call is made for them at all. That amplification is
    fine, but it must not relabel a per-minute blip as a spent day."""
    calls = []
    burst = FakeResp({"errors": {"rateLimit": "Too many requests"}, "response": []})
    _install(monkeypatch, 10, burst, calls)
    tracker.poll()
    assert len(calls) == 1, "the latch must stop further calls this cycle"
    # Every fixture after the first is labelled by the latch, not by a call.
    assert set(tracker.enrich_status.values()) == {lt.RATE_LIMITED}


def test_empty_response_is_real_no_coverage(tracker, monkeypatch):
    """A clean 200 with no rows is the one case that IS a coverage gap."""
    calls = []
    _install(monkeypatch, 3, FakeResp({"response": []}), calls)
    tracker.poll()
    assert set(tracker.enrich_status.values()) == {"empty"}
    assert len(calls) == 3


def test_uncovered_league_stops_being_retried(tracker, monkeypatch):
    """Coverage is a property of the competition, so learn it once."""
    calls = []
    # all four fixtures share one league, already struck out three times
    _install(monkeypatch, 4, FakeResp({"response": []}), calls, league="Dead League")
    tracker._empty_leagues["Dead League"] = {1, 2, 3}
    tracker._empty_league_at["Dead League"] = time.time()
    tracker.poll()
    assert calls == [], "a league proven uncovered must not be paid for again"
    assert tracker.last_enrich_report.get("league uncovered") == 4


def test_a_struck_out_league_gets_another_chance_eventually(tracker, monkeypatch):
    """The strikes used to be permanent and the dict was never cleared, so one
    bad hour blacklisted a competition for the life of a process that runs for
    weeks. On 2026-08-20 that had MLS marked uncovered while api-football was
    serving its statistics — a restart was the only cure."""
    calls = []
    _install(monkeypatch, 4, FakeResp(_stats_payload()), calls, league="Dead League")
    tracker._empty_leagues["Dead League"] = {1, 2, 3}
    tracker._empty_league_at["Dead League"] = time.time() - lt.EMPTY_LEAGUE_TTL_S - 1
    tracker.poll()
    assert calls, "an expired blacklist must be retried"


def test_a_league_the_api_confirms_is_covered_is_never_blacklisted(tracker, monkeypatch):
    """An empty response from a covered league means 'not published yet' — it is
    minute 8 of a small fixture — not 'this competition has no statistics'."""
    calls = []
    _install(monkeypatch, 4, FakeResp({"response": []}), calls, league="Real League")
    tracker._league_stats_coverage[0] = True      # every fake fixture shares league id
    monkeypatch.setattr(tracker, "league_has_stats", lambda lid: True)
    tracker.poll()
    assert len(calls) == 4                        # asked, got nothing, fine
    assert tracker._empty_leagues == {}, "a covered league must never be struck"


def test_a_league_the_api_says_is_uncovered_is_never_paid_for(tracker, monkeypatch):
    """The API will answer this directly, so there is no reason to burn three
    fixtures guessing at it."""
    calls = []
    _install(monkeypatch, 4, FakeResp(_stats_payload()), calls, league="No Stats League")
    monkeypatch.setattr(tracker, "league_has_stats", lambda lid: False)
    tracker.poll()
    assert calls == []
    assert tracker.last_enrich_report.get("league uncovered (api)") == 4


# ── stats have to survive a poll that did not re-fetch them ──────────────────
# The TTL exists so we do not pay for the same fixture every 60 seconds. But
# every poll builds a NEW snapshot, and for two polls out of three nothing used
# to fill it: a match measured at minute 15 reported shots 0-0 and no pressure at
# all at 16' and 17', then measured again at 18'. Both first-half agents take
# their entry decision inside a ten-minute window, so this alone was enough to
# produce zero entries in a day. Found live on 2026-08-20.

def test_stats_survive_a_ttl_skipped_poll(tracker, monkeypatch):
    calls = []
    _install(monkeypatch, 1, FakeResp(_stats_payload()), calls)

    tracker.poll()                       # minute 80: pays for the stats
    assert len(calls) == 1
    first = tracker.snapshots[1000][-1]
    assert first.home_shots_total == 7

    tracker.poll()                       # inside the TTL: no second call
    assert len(calls) == 1
    second = tracker.snapshots[1000][-1]
    assert second is not first
    assert second.home_shots_total == 7, "the last known stats must carry forward"
    assert tracker.get_signals(1000).has_stats


def test_a_carried_snapshot_says_how_old_its_numbers_are(tracker, monkeypatch):
    """Carrying stale numbers silently would be its own bug: anything turning
    totals into a per-minute rate has to divide by the minute they were true."""
    calls = []
    _install(monkeypatch, 1, FakeResp(_stats_payload()), calls)
    tracker.poll()

    # the same fixture, three minutes later, still inside the TTL
    monkeypatch.setattr(lt.requests, "get", lambda url, **kw: (
        calls.append(kw["params"]["fixture"]) or FakeResp(_stats_payload())
    ) if "statistics" in url else FakeResp(_live_payload(1, minute=83)))
    tracker.poll()

    sig = tracker.get_signals(1000)
    assert sig.minute == 83
    assert sig.stats_minute == 80          # not 83 — the numbers are three minutes old


def test_goals_are_never_carried_forward(tracker, monkeypatch):
    """Only the stat block is stale-able. The score comes from the live feed on
    every poll and carrying it would freeze a match that had just scored."""
    calls = []
    _install(monkeypatch, 1, FakeResp(_stats_payload()), calls)
    tracker.poll()

    payload = _live_payload(1)
    payload["response"][0]["goals"] = {"home": 1, "away": 0}
    monkeypatch.setattr(lt.requests, "get",
                        lambda url, **kw: FakeResp(payload) if "statistics" not in url
                        else FakeResp(_stats_payload()))
    tracker.poll()
    assert tracker.snapshots[1000][-1].home_goals == 1


def test_a_carried_stat_block_never_counts_as_this_poll_s_stats(tracker, monkeypatch):
    """The regression that killed strategy 16 between 2026-08-20 and 08-25.

    The enrichment gate read `latest.home_shots_total` to decide the live feed
    had already supplied stats inline. Once _carry_stats_forward landed, that
    field held the carried copy of an earlier fetch, so the gate skipped the
    fixture forever: one paid call per match, then frozen totals for the rest of
    it. The TTL is supposed to be what throttles refetching, and it must stay
    that way — after the TTL expires the fixture has to be paid for again.
    """
    calls = []
    _install(monkeypatch, 1, FakeResp(_stats_payload()), calls)

    tracker.poll()
    assert calls == [1000]

    # Past the TTL. Nothing about the snapshot holding carried stats may stop
    # this fixture being refetched.
    tracker._enrich_at[1000] = time.time() - lt.ENRICH_TTL_S - 1
    tracker.poll()
    assert calls == [1000, 1000], "a carried stat block suppressed the refetch"


def test_inline_stats_still_skip_the_paid_call(tracker, monkeypatch):
    """The gate's real job, which the fix must not throw away: when the live
    feed carries statistics itself, no /fixtures/statistics call is worth
    paying for."""
    calls = []
    payload = _live_payload(1)
    payload["response"][0]["statistics"] = [
        {"team": {"name": "Home0"}, "statistics": [{"type": "Total Shots", "value": 9}]},
        {"team": {"name": "Away0"}, "statistics": [{"type": "Total Shots", "value": 2}]},
    ]
    monkeypatch.setattr(lt.requests, "get", lambda url, **kw: (
        calls.append(kw["params"]["fixture"]) or FakeResp(_stats_payload())
    ) if "statistics" in url else FakeResp(payload))

    tracker.poll()
    assert calls == [], "paid for stats the live feed had already given us"
    assert tracker.snapshots[1000][-1].home_shots_total == 9


def test_a_window_differencing_one_fetch_against_itself_is_not_a_measurement(tracker):
    """Frozen totals made every window delta 0, which the danger index scored as
    its possession term alone — exactly 5.0 out of 100 — and both has_stats and
    has_window went on reporting True. The row then read as a measured dead
    match instead of an unmeasured one, and against MIN_PRESSURE it could never
    enter. A baseline sharing the latest snapshot's fetch is no baseline.
    """
    fetched = time.time()
    for minute in range(60, 81):
        tracker.snapshots[1000].append(lt.StatSnapshot(
            minute=minute, timestamp=fetched, home_shots_total=5, away_shots_total=3,
            home_shots_on=1, home_corners=2, stats_minute=60, stats_fetched_at=fetched))
    tracker.fixture_info[1000] = {"home": "Home0", "away": "Away0", "league": "L"}

    sig = tracker.get_signals(1000)
    assert sig.has_stats                      # there IS a stat block
    assert sig.stats_frozen
    assert not sig.has_window
    # and no fabricated surge from the scaled-totals fallback either
    assert sig.home_shots_on_window == 0
    assert sig.home_corners_window == 0


# ── a refused live poll is not a quiet evening ───────────────────────────────

def test_refused_live_poll_is_not_reported_as_zero_fixtures():
    """api-football answers a refusal with HTTP 200 and an empty `response`.

    For three evenings (2026-08-31 / 09-01 / 09-02) that read as "0 live
    fixtures" once a minute while the agent recorded nothing at all. The one
    call every cycle depends on was the one call with no error handling.
    """
    import live_tracker as lt

    class _Resp:
        status_code = 200
        headers = {"x-ratelimit-requests-limit": "75000",
                   "x-ratelimit-requests-remaining": "74999"}

        @staticmethod
        def json():
            return {"errors": {"requests": "You have reached the request limit "
                                           "for the day"},
                    "results": 0, "response": []}

    tracker = lt.LiveMatchTracker()
    tracker.api_key = "test-key"
    original = lt.requests.get
    lt.requests.get = lambda *a, **k: _Resp()
    try:
        out = tracker.poll()
    finally:
        lt.requests.get = original

    # Empty because the ESPN fallback is stubbed to nothing by `_no_espn`; the
    # assertions here are about api-football's own bookkeeping surviving a
    # refusal, which it must do whether or not a second source answers.
    assert out == {}
    # the refusal has to leave a mark the next cycle can act on, or the agent
    # spends the outage hammering an API that is saying no
    assert tracker._quota_spent_until > time.time() + 1800
    assert tracker._quota_reason == lt.DAILY_EXHAUSTED
    # ...and it is NOT a transport failure: restarting the process cannot help,
    # so DEAD_POLLS_BEFORE_EXIT must not be armed by it
    assert tracker.last_poll_failed is False


def test_a_genuinely_quiet_feed_still_reads_as_zero():
    """The counterpart: no football on is a real answer, not an outage."""
    import live_tracker as lt

    class _Resp:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"errors": [], "results": 0, "response": []}

    tracker = lt.LiveMatchTracker()
    tracker.api_key = "test-key"
    original = lt.requests.get
    lt.requests.get = lambda *a, **k: _Resp()
    try:
        out = tracker.poll()
    finally:
        lt.requests.get = original

    assert out == {}
    assert tracker._quota_spent_until == 0      # nothing armed


# ── the ESPN fallback ────────────────────────────────────────────────────────

def test_a_refused_poll_falls_back_to_espn(tracker, monkeypatch):
    """2026-09-06 was the fourth evening in a week where the allowance ran out
    mid-match and every arm went blind for hours, because a refusal returned {}.
    A degraded reading recorded AS degraded beats no reading."""
    import espn_stats

    monkeypatch.setattr(lt.requests, "get", lambda *a, **k: FakeResp(
        {"errors": {"requests": "You have reached the request limit for the day"},
         "response": []}))
    monkeypatch.setattr(lt.espn_stats, "live_fixtures", lambda *a, **k: [
        espn_stats.EspnFixture(
            event_id="401882892", league_code="esp.1", league="LaLiga",
            home="Espanyol", away="Sevilla", minute=67, state="in", detail="67'",
            home_stats=espn_stats.EspnTeamStats(shots_on=4, shots_total=9,
                                                corners=3, possession=58.0),
            away_stats=espn_stats.EspnTeamStats(shots_on=1, shots_total=4,
                                                corners=1, possession=42.0),
        )
    ])

    out = tracker.poll()
    assert out, "the fallback has to produce signals, not an empty dict"

    fid, sig = next(iter(out.items()))
    # Namespaced negative: ESPN's ids and api-football's do not collide today,
    # but a collision would splice two matches' histories into one.
    assert fid < 0
    assert sig.home == "Espanyol" and sig.away == "Sevilla"
    assert sig.minute == 67
    # The row has to say it is a different measurement, or the two populations
    # pool into one meaningless number.
    assert sig.stats_source == "espn"
    assert sig.has_inside is False


def test_the_espn_fallback_does_not_hide_a_spent_day(tracker, monkeypatch):
    """Falling back must not clear the flag that stops us hammering an API which
    is saying no — the outage is still an outage."""
    monkeypatch.setattr(lt.requests, "get", lambda *a, **k: FakeResp(
        {"errors": {"requests": "You have reached the request limit for the day"},
         "response": []}))
    tracker.poll()
    assert tracker._quota_reason == lt.DAILY_EXHAUSTED
    assert tracker._quota_spent_until > time.time() + 1800
