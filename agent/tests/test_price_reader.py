"""Price Reader (db/089): an agent that reads only the price, in every sport.

What these pin: it prices both tokens of a market from one Gamma quote and
never reads an empty side as a price; one position per arm per GAME, however
many sub-events Polymarket splits the game into; 'post' needs Polymarket's own
ended flag; the move arms read their own memory; the CLOB book, not Gamma,
decides the entry; fees are charged both ways; and the daily review only moves
an arm on the evidence its rules name, never on "the best of the lot".
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import price_reader as pr  # noqa: E402
import price_reader_review as rv  # noqa: E402

NOW = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(cid, q, outcomes, bid, ask, typ="moneyline", liq=5000, item=None, ch=None, toks=None):
    return {"conditionId": cid, "question": q, "outcomes": str(outcomes).replace("'", '"'),
            "clobTokenIds": str(toks or [cid + "-a", cid + "-b"]).replace("'", '"'),
            "bestBid": bid, "bestAsk": ask, "sportsMarketType": typ, "liquidityNum": liq,
            "groupItemTitle": item, "oneHourPriceChange": ch, "acceptingOrders": True, "closed": False}


def _event(eid, title, start, markets, tags=("sports", "soccer"), game=None, live=False, ended=False):
    return {"id": eid, "title": title, "startTime": _iso(start), "markets": markets,
            "tags": [{"slug": t} for t in tags], "gameId": game, "live": live, "ended": ended,
            "seriesSlug": "test-league", "slug": f"ev-{eid}"}


# ── reading a market ─────────────────────────────────────────────────────────

def test_both_tokens_are_priced_from_one_quote_and_an_empty_side_is_not_a_price():
    ev = _event(1, "A vs B", NOW + timedelta(hours=1),
                [_market("c1", "A vs B: O/U 2.5", ["Over", "Under"], 0.40, 0.42, "totals")])
    over, under = pr.quotes_of(ev)
    assert (over.bid, over.ask) == (0.40, 0.42)
    assert round(under.bid, 3) == 0.58 and round(under.ask, 3) == 0.60
    assert under.family == "totals" and not under.period

    ev = _event(2, "C vs D", NOW + timedelta(hours=1),
                [_market("c2", "C vs D", ["C", "D"], 0, 1)])            # Gamma's empty book
    c, d = pr.quotes_of(ev)
    assert c.ask is None and c.bid is None and d.ask is None and d.bid is None
    assert c.mid is None


def test_market_families_come_from_the_type_not_the_text():
    assert pr.family_of({"sportsMarketType": "soccer_exact_score"}) == ("exact", False)
    assert pr.family_of({"sportsMarketType": "first_half_totals"}) == ("totals", True)
    assert pr.family_of({"sportsMarketType": "total_corners",
                         "question": "X vs Y: O/U 7.5 Total Corners"})[0] == "corners"
    assert pr.family_of({"sportsMarketType": "q3_spreads"}) == ("spreads", True)
    assert pr.family_of({"sportsMarketType": None, "question": "Will Everton win?"})[0] == "winner"
    assert pr.family_of({"sportsMarketType": "rebounds"})[0] == "props"


def test_a_sport_is_read_from_the_tags_and_esports_wins_over_a_generic_tag():
    assert pr.sport_of({"tags": [{"slug": "sports"}, {"slug": "soccer"}]}) == "soccer"
    assert pr.sport_of({"tags": [{"slug": "sports"}, {"slug": "esports"}, {"slug": "counter-strike-2"}]}) == "esports"
    assert pr.sport_of({"tags": [{"slug": "nhl"}]}) == "hockey"
    assert pr.sport_of({"tags": []}) == "other"


def test_post_needs_polymarket_s_ended_flag_and_the_clock_only_rules_out():
    start = NOW - timedelta(minutes=200)
    assert pr.event_phase(_event(1, "x", start, [], ended=True), "soccer", NOW).phase == "post"
    # 200 min after a football kick-off with no flag is not "post": unknown
    assert pr.event_phase(_event(1, "x", start, []), "soccer", NOW).phase == "unknown"
    ph = pr.event_phase(_event(1, "x", NOW - timedelta(minutes=92), [], live=True), "soccer", NOW)
    assert ph.phase == "live" and 0.79 < ph.frac < 0.81
    assert pr.event_phase(_event(1, "x", NOW + timedelta(minutes=30), []), "soccer", NOW).phase == "pre"
    # a sport whose length is unknown is never "late"
    assert pr.event_phase(_event(1, "x", NOW - timedelta(minutes=60), [], live=True), "mma", NOW).frac is None


# ── what an arm would buy ────────────────────────────────────────────────────

def test_one_position_per_arm_per_game_across_polymarket_s_sub_events():
    start = NOW + timedelta(minutes=60)
    main = _event(1, "A vs B", start, [_market("c1", "Will A win?", ["Yes", "No"], 0.80, 0.81, liq=9000)],
                  game=777)
    more = _event(2, "A vs B - More Markets", start,
                  [_market("c2", "A vs B: O/U 3.5", ["Over", "Under"], 0.15, 0.16, "totals", liq=4000)], game=777)
    params = pr.BASE_ARMS["pre_favourite"][1]
    cands = pr.arm_candidates(params, [main, more], pr.PriceMemory(), NOW)
    assert len(cands) == 1
    assert cands[0].quote.condition_id == "c1"            # the more liquid market
    assert cands[0].quote.event_id == "g:777"
    # a game with no gameId is keyed by its title before " - " and its start
    a = _event(3, "C vs D", start, [], game=None)
    b = _event(4, "C vs D - Exact Score", start, [], game=None)
    assert pr.match_key(a) == pr.match_key(b)


def test_the_band_the_family_and_the_side_are_the_arm_s():
    start = NOW + timedelta(minutes=60)
    grid = _event(1, "A vs B - Exact Score", start, [
        _market("e1", "Exact Score: A 1 - 1 B?", ["Yes", "No"], 0.07, 0.08, "soccer_exact_score", item="A 1 - 1 B"),
        _market("e2", "Exact Score: A 5 - 0 B?", ["Yes", "No"], 0.005, 0.01, "soccer_exact_score")])
    cands = pr.arm_candidates(pr.BASE_ARMS["grid_no"][1], [grid], pr.PriceMemory(), NOW)
    assert len(cands) == 1
    q = cands[0].quote
    assert q.outcome == "No" and q.condition_id == "e1" and round(q.ask, 2) == 0.93
    assert q.label == "Not 1-1 (exact score)"
    assert pr.pick_label({"question": "Will Everton FC win on 2026-11-06?", "groupItemTitle": "Everton FC"},
                         "No", "winner") == "Everton FC not to win"
    assert pr.pick_label({"question": "Will X vs. Y end in a draw?", "groupItemTitle": "Draw (X vs. Y)"},
                         "No", "winner") == "Not a draw"
    assert pr.pick_label({"question": "X vs Y: O/U 2.5", "groupItemTitle": "O/U 2.5"}, "Over", "totals") \
        == "Over · O/U 2.5"
    # pre_favourite does not take a period market, nor an exact score
    ht = _event(2, "A vs B - Halftime Result", start, [
        _market("h1", "A leading at halftime?", ["Yes", "No"], 0.80, 0.81, "soccer_halftime_result")])
    assert pr.arm_candidates(pr.BASE_ARMS["pre_favourite"][1], [ht, grid], pr.PriceMemory(), NOW) == []
    # outside the 90-minute window
    far = _event(3, "E vs F", NOW + timedelta(minutes=150), [_market("f1", "E vs F", ["E", "F"], 0.80, 0.81)])
    assert pr.arm_candidates(pr.BASE_ARMS["pre_favourite"][1], [far], pr.PriceMemory(), NOW) == []


def test_the_move_arms_read_their_own_memory_and_wait_until_it_reaches_back():
    ev = lambda bid, ask: _event(1, "A vs B", NOW - timedelta(minutes=50),   # noqa: E731
                                 [_market("m1", "A vs B", ["A", "B"], bid, ask)], live=True, game=5)
    mem = pr.PriceMemory()
    params = pr.BASE_ARMS["live_momentum"][1]
    for i, (b, a) in enumerate([(0.40, 0.41), (0.42, 0.43), (0.47, 0.48), (0.53, 0.54)]):
        t = NOW - timedelta(minutes=10) + timedelta(minutes=10 * i / 3)
        mem.observe(pr.quotes_of(ev(b, a)), t)
    assert round(mem.move("m1-a", NOW, 10), 3) == 0.13
    cands = pr.arm_candidates(params, [ev(0.53, 0.54)], mem, NOW)
    assert len(cands) == 1 and cands[0].quote.token_id == "m1-a" and cands[0].move_pp > 0.10
    # the fade arm buys the side that fell: B went 0.59 → 0.46
    fade = pr.arm_candidates(pr.BASE_ARMS["live_fade"][1], [ev(0.53, 0.54)], mem, NOW)
    assert len(fade) == 1 and fade[0].quote.token_id == "m1-b"
    # with no memory of the window, nothing
    assert pr.arm_candidates(params, [ev(0.53, 0.54)], pr.PriceMemory(), NOW) == []


def test_pre_steam_uses_gamma_s_hour_until_its_own_memory_reaches_back():
    ev = _event(1, "A vs B", NOW + timedelta(minutes=100),
                [_market("s1", "A vs B", ["A", "B"], 0.50, 0.51, ch=0.05)], game=9)
    cands = pr.arm_candidates(pr.BASE_ARMS["pre_steam"][1], [ev], pr.PriceMemory(), NOW)
    assert len(cands) == 1 and cands[0].quote.token_id == "s1-a"


# ── the book decides ─────────────────────────────────────────────────────────

def test_the_clob_book_not_gamma_decides_the_entry():
    p = pr.BASE_ARMS["pre_favourite"][1]
    good = {"best_bid": 0.80, "best_ask": 0.81, "ask_depth_usd": 400}
    assert pr.entry_verdict(p, good) is None
    assert pr.entry_verdict(p, None) == "no book"
    assert pr.entry_verdict(p, {**good, "best_bid": None}) == "one-sided book"
    assert pr.entry_verdict(p, {**good, "best_bid": 0.70}).startswith("spread")
    assert pr.entry_verdict(p, {**good, "ask_depth_usd": 20}).startswith("ask depth")
    assert pr.entry_verdict(p, {**good, "best_bid": 0.94, "best_ask": 0.95}).startswith("ask 0.950")


def test_parse_book_sorts_the_sides_and_keeps_an_empty_one_empty():
    b = pr.parse_book({"bids": [{"price": "0.01", "size": "100"}, {"price": "0.45", "size": "10"}],
                       "asks": [{"price": "0.99", "size": "5"}, {"price": "0.47", "size": "200"}]})
    assert b["best_bid"] == 0.45 and b["best_ask"] == 0.47
    assert round(b["ask_depth_usd"], 2) == round(0.47 * 200 + 0.99 * 5, 2)
    assert pr.parse_book({"bids": [], "asks": []})["best_ask"] is None


def test_the_fee_is_paid_on_the_way_in_and_on_the_way_out():
    assert round(pr.cost_per_share(0.50), 4) == 0.5125
    assert round(pr.sale_per_share(0.50), 4) == 0.4875
    shares = pr.shares_for(1.0, 0.95)
    assert round(shares * 1.0, 4) == round(pr.net_odds(0.95), 4)   # a win pays the net odds
    assert pr.net_odds(0.95) < 1 / 0.95
    assert pr.sale_verdict({"best_bid": None, "best_ask": 0.5}) == "no bid"
    assert pr.sale_verdict({"best_bid": 0.30, "best_ask": 0.45}).startswith("spread")
    assert pr.sale_verdict({"best_bid": 0.40, "best_ask": 0.42}) is None


def test_every_base_arm_trades_and_every_variant_starts_in_shadow():
    arms = pr.seed_arms()
    keys = [a["key"] for a in arms]
    assert len(keys) == len(set(keys))
    assert {a["key"] for a in arms if a["mode"] == "active"} == set(pr.BASE_ARMS)
    for a in arms:
        if a["mode"] == "shadow":
            assert a["params"] != pr.BASE_ARMS[a["base_key"]][1]


# ── the record ───────────────────────────────────────────────────────────────

def test_the_record_has_an_interval_only_from_two_bets():
    assert pr.record([], [])["yield"] is None
    r = pr.record([0.5], [1.0])
    assert r["yield"] == 0.5 and r["lo90"] is None
    r = pr.record([1.0, -1.0, 1.0, -1.0], [1, 1, 1, 1])
    assert r["yield"] == 0 and r["lo90"] < 0 < r["hi90"]
    assert abs(pr.prob_positive(r) - 0.5) < 1e-9


# ── the daily review ─────────────────────────────────────────────────────────

def _arm(i, key, base, mode="active", cap=25, since_days=10, excl=()):
    t = NOW - timedelta(days=since_days)
    return {"id": i, "key": key, "base_key": base, "label": key, "mode": mode, "mode_since": t,
            "created_at": t, "daily_cap": cap, "excluded_sports": list(excl), "params": {}}


def _pos(arm_id, pl, sport="soccer", days_ago=1.0, booked=True):
    return {"arm_id": arm_id, "pl": pl, "stake": 1.0, "status": "settled", "sport": sport,
            "entered_at": NOW - timedelta(days=days_ago), "booked": booked}


def test_a_clearly_losing_arm_stops_booking_and_a_short_record_is_left_alone():
    arms = [_arm(1, "a", "a")]
    losing = [_pos(1, -1.0 if i % 2 else 0.2) for i in range(60)]          # yield −40%
    ch, _ = rv.decide(arms, losing, NOW)
    assert [c["what"] for c in ch if c["what"] != "cap"] == ["stop"]
    assert next(c for c in ch if c["what"] == "stop")["mode"] == "shadow"
    ch, _ = rv.decide(arms, losing[:30], NOW)                               # 30 bets: too few to judge
    assert [c for c in ch if c["what"] == "stop"] == []


def test_an_arm_moved_in_the_last_three_days_is_not_moved_again():
    arms = [_arm(1, "a", "a", since_days=1)]
    ch, _ = rv.decide(arms, [_pos(1, -1.0 if i % 2 else 0.2) for i in range(60)], NOW)
    assert [c for c in ch if c["what"] == "stop"] == []


def test_a_variant_is_promoted_only_by_a_clear_margin_on_the_same_days():
    arms = [_arm(1, "a", "a"), _arm(2, "a@x=1", "a", mode="shadow")]
    base = [_pos(1, -0.1 if i % 2 else 0.1) for i in range(120)]           # 0%, not clearly losing
    near = [_pos(2, -0.08 if i % 2 else 0.1) for i in range(120)]          # +1%: inside the noise
    ch, _ = rv.decide(arms, base + near, NOW)
    assert [c for c in ch if c["what"] in ("promote", "replaced")] == []
    better = [_pos(2, 0.30 if i % 3 else -0.10) for i in range(120)]       # about +16.7%
    ch, _ = rv.decide(arms, base + better, NOW)
    whats = {c["what"]: c for c in ch}
    assert whats["promote"]["arm_id"] == 2 and whats["promote"]["mode"] == "active"
    assert whats["replaced"]["arm_id"] == 1 and whats["replaced"]["mode"] == "shadow"


def test_a_stopped_group_comes_back_only_with_an_interval_wholly_above_zero():
    arms = [_arm(1, "a", "a", mode="shadow"), _arm(2, "a@x=1", "a", mode="shadow")]
    flat = [_pos(1, 0.2 if i % 2 else -0.15) for i in range(120)]          # +2.5% but noisy
    ch, _ = rv.decide(arms, flat, NOW)
    assert [c for c in ch if c["what"] == "resume"] == []
    clear = [_pos(2, 0.10 if i % 5 else -0.05) for i in range(120)]
    ch, _ = rv.decide(arms, flat + clear, NOW)
    assert [c["arm_id"] for c in ch if c["what"] == "resume"] == [2]


def test_one_losing_sport_is_dropped_from_an_arm_and_comes_back_on_evidence():
    arms = [_arm(1, "a", "a")]
    ps = [_pos(1, 0.2, "soccer") for _ in range(100)] + \
         [_pos(1, -0.5 if i % 2 else 0.1, "tennis") for i in range(50)]      # the arm is +6.7% overall
    ch, _ = rv.decide(arms, ps, NOW)
    sp = [c for c in ch if c["what"] == "sports"]
    assert sp and sp[0]["excluded_sports"] == ["tennis"]
    arms = [_arm(1, "a", "a", excl=["tennis"])]
    ps = [_pos(1, 0.1, "soccer") for _ in range(50)] + [_pos(1, 0.1 if i % 4 else 0.05, "tennis") for i in range(45)]
    ch, _ = rv.decide(arms, ps, NOW)
    sp = [c for c in ch if c["what"] == "sports"]
    assert sp and sp[0]["excluded_sports"] == []


def test_the_daily_cap_follows_the_evidence_within_its_bounds():
    arms = [_arm(1, "a", "a", cap=25)]
    ch, _ = rv.decide(arms, [_pos(1, 0.1) for _ in range(10)], NOW)
    assert [c for c in ch if c["what"] == "cap"] == []                    # exploring: stays 25
    ch, _ = rv.decide(arms, [_pos(1, 0.10 if i % 5 else -0.05) for i in range(40)], NOW)
    cap = [c for c in ch if c["what"] == "cap"][0]["daily_cap"]
    assert rv.CAP_MIN <= cap <= rv.CAP_MAX and cap > 25


def test_the_diary_says_it_is_a_record_under_two_hundred_bets():
    arms = [_arm(1, "a", "a")]
    ps = [_pos(1, 0.1) for _ in range(12)]
    ch, recs = rv.decide(arms, ps, NOW)
    text = rv.diary(arms, ps, recs, ch, NOW, 3, NOW - timedelta(days=2))
    assert text.startswith("Day 3.")
    assert "12 of the 200 settled bets a verdict needs" in text
    assert "No change to what it trades today" in text
