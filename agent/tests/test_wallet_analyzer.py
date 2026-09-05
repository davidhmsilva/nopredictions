"""
Tests for the Polymarket wallet analyser's reconstruction.

Every case here is a bug that would produce a plausible-looking wallet profile
rather than an obvious failure — a wrong entry price, a redemption booked on the
wrong token, an off-feed share counted as free money. Those are the errors that
get believed.

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_wallet_analyzer.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wallet_analyzer as wa  # noqa: E402

CID = "0xcondition"
TOK_YES = "111"
TOK_NO = "222"

MARKET = {
    "conditionId": CID,
    "clobTokenIds": '["111", "222"]',
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["1", "0"]',
    "closed": True,
    "gameStartTime": "2026-05-20 18:00:00+00",
    "events": [{"ticker": "epl-ars-che-2026-05-20"}],
    "sportsMarketType": "moneyline",
}
KICKOFF = 1779300000  # 2026-05-20 18:00 UTC


def trade(ts, side, shares, usd, asset=TOK_YES, price=None, outcome="Yes"):
    return {"timestamp": ts, "type": "TRADE", "side": side, "asset": asset,
            "conditionId": CID, "size": shares, "usdcSize": usd,
            "price": price if price is not None else round(usd / shares, 2),
            "outcome": outcome, "outcomeIndex": 0 if asset == TOK_YES else 1,
            "title": "Will Arsenal win?", "eventSlug": "epl-ars-che-2026-05-20",
            "transactionHash": f"0x{ts}"}


def redeem(ts, shares, usd, outcome_index=0, outcome="Yes"):
    return {"timestamp": ts, "type": "REDEEM", "side": "", "asset": "",
            "conditionId": CID, "size": shares, "usdcSize": usd, "price": 0,
            "outcome": outcome, "outcomeIndex": outcome_index,
            "title": "Will Arsenal win?", "eventSlug": "epl-ars-che-2026-05-20",
            "transactionHash": f"0x{ts}"}


# ── FIFO ─────────────────────────────────────────────────────────────────────

def test_fifo_matches_oldest_lot_first():
    rows = [trade(1000, "BUY", 100, 20.0),     # 0.20
            trade(2000, "BUY", 100, 50.0),     # 0.50
            trade(3000, "SELL", 100, 40.0)]    # 0.40 — must close the 0.20 lot
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    closed = [l for l in lots if l.exit_kind == "sell"]
    assert len(closed) == 1
    assert closed[0].entry_price == pytest.approx(0.20)
    assert closed[0].pnl == pytest.approx(20.0)


def test_a_sell_spanning_two_lots_splits_them():
    rows = [trade(1000, "BUY", 100, 10.0),
            trade(2000, "BUY", 100, 30.0),
            trade(3000, "SELL", 150, 60.0)]    # 0.40 across both lots
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    sells = sorted((l for l in lots if l.exit_kind == "sell"), key=lambda l: l.entry_price)
    assert [round(l.shares) for l in sells] == [100, 50]
    assert sum(l.pnl for l in sells) == pytest.approx(60.0 - 10.0 - 15.0)


def test_the_price_field_is_ignored_in_favour_of_the_cash():
    """PM rounds `price` to the displayed tick — 17.5% of GSX-'s fills disagree
    with their own usdcSize. The money moved is the only price that is real."""
    rows = [trade(1000, "BUY", 30, 1.1412, price=0.04),
            trade(2000, "SELL", 30, 1.1412, price=0.04)]
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    assert lots[0].entry_price == pytest.approx(0.03804)
    assert lots[0].pnl == pytest.approx(0.0)


# ── redemptions ──────────────────────────────────────────────────────────────

def test_redeem_lands_on_the_token_its_outcome_index_names():
    """REDEEM rows carry no `asset`. Attributed by title or by guesswork the
    payout lands on the other side of the market and both legs are wrong."""
    rows = [trade(1000, "BUY", 100, 20.0, asset=TOK_YES),
            trade(1000, "BUY", 100, 70.0, asset=TOK_NO, outcome="No"),
            redeem(9000, 100, 100.0, outcome_index=0)]
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    redeemed = [l for l in lots if l.exit_kind == "redeem"]
    assert len(redeemed) == 1
    assert redeemed[0].asset == TOK_YES
    assert redeemed[0].pnl == pytest.approx(80.0)


def test_unredeemed_winner_is_valued_at_settlement_and_flagged_as_a_mark():
    rows = [trade(1000, "BUY", 100, 20.0)]
    lots, flows = wa.build_lots(rows, {CID: MARKET})
    assert lots[0].exit_kind == "resolved"
    assert lots[0].exit_price == 1.0
    assert flows["open_value"] == pytest.approx(100.0)


def test_a_losing_leftover_is_worth_zero_not_its_last_price():
    rows = [trade(1000, "BUY", 100, 70.0, asset=TOK_NO, outcome="No")]
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    assert lots[0].exit_price == 0.0
    assert lots[0].pnl == pytest.approx(-70.0)


# ── off-feed shares ──────────────────────────────────────────────────────────

def test_shares_sold_but_never_bought_are_booked_flat_never_free():
    """Neg-risk conversions mint shares outside the activity feed. Booked at
    zero cost they become pure invented profit — an error that can only run one
    way, which is the shape of the bug that paid a losing Over 1.5 at 4.35."""
    rows = [trade(3000, "SELL", 100, 90.0)]
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    assert len(lots) == 1
    assert lots[0].exit_kind == "unmatched"
    assert lots[0].pnl == pytest.approx(0.0)


def test_unmatched_lots_are_excluded_from_capital_and_yield():
    rows = [trade(1000, "BUY", 100, 20.0),
            trade(2000, "SELL", 100, 40.0),
            trade(3000, "SELL", 500, 250.0)]   # off-feed shares
    profile = wa.analyse("0x" + "a" * 40, rows, {CID: MARKET})
    assert profile["totals"]["deployed"] == pytest.approx(20.0)
    assert profile["totals"]["pnl"] == pytest.approx(20.0)
    assert profile["coverage"]["unmatched_lots"] == 1
    # …and the optimistic bound is reported so the leaderboard can be bracketed.
    assert profile["totals"]["pnl_high"] == pytest.approx(270.0)
    assert sum(w["share_of_cost"] for w in profile["timing"].values()) == pytest.approx(100.0)


# ── timing ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("offset_min,window", [
    (-60, "prematch"), (1, "in_match"), (109, "in_match"),
    (115, "whistle"), (129, "whistle"), (200, "settle"),
])
def test_entry_window_is_measured_from_the_listed_kick_off(offset_min, window):
    ts = KICKOFF + offset_min * 60
    rows = [trade(ts, "BUY", 100, 20.0), trade(ts + 60, "SELL", 100, 25.0)]
    profile = wa.analyse("0x" + "a" * 40, rows, {CID: MARKET})
    assert profile["timing"][window]["lots"] == 1


def test_a_market_with_no_kick_off_time_is_unknown_not_prematch():
    market = {**MARKET}
    market.pop("gameStartTime")
    market.pop("events")
    rows = [trade(1000, "BUY", 100, 20.0), trade(2000, "SELL", 100, 25.0)]
    profile = wa.analyse("0x" + "a" * 40, rows, {CID: market})
    assert profile["timing"]["unknown"]["lots"] == 1
    assert profile["timing"]["prematch"]["lots"] == 0


# ── reporting ────────────────────────────────────────────────────────────────

def test_the_ticket_is_a_fill_not_a_fifo_fragment():
    """One $100 buy closed by four sells is a $100 ticket, not four $25 ones."""
    rows = [trade(1000, "BUY", 100, 100.0)] + [
        trade(2000 + i, "SELL", 25, 25.0) for i in range(4)]
    profile = wa.analyse("0x" + "a" * 40, rows, {CID: MARKET})
    assert profile["totals"]["median_ticket"] == pytest.approx(100.0)


def test_bootstrap_is_deterministic():
    rows = []
    for i in range(40):
        rows += [trade(1000 + i * 10, "BUY", 100, 20.0),
                 trade(1005 + i * 10, "SELL", 100, 22.0)]
    a = wa.analyse("0x" + "a" * 40, rows, {CID: MARKET})["bootstrap"]
    b = wa.analyse("0x" + "a" * 40, rows, {CID: MARKET})["bootstrap"]
    assert a == b


def test_mulberry32_matches_the_reference_stream():
    """The site's TypeScript runs the same PRNG. If these drift, the two
    implementations report different confidence intervals for the same wallet
    and `--verify-site` blames the analysis instead of the RNG.

    The same five numbers are pinned in site/app/lib/wallet.ts."""
    rng = wa.Mulberry32(42)
    assert [round(rng.next(), 12) for _ in range(5)] == [
        0.60110375192, 0.448290558998, 0.85246579349, 0.669734041439, 0.174813898746]


# ── merges ───────────────────────────────────────────────────────────────────

def merge(ts, shares, usd):
    return {"timestamp": ts, "type": "MERGE", "side": "", "asset": "",
            "conditionId": CID, "size": shares, "usdcSize": usd, "price": 0,
            "outcome": "", "outcomeIndex": 999, "title": "Will Arsenal win?",
            "eventSlug": "epl-ars-che-2026-05-20", "transactionHash": f"0x{ts}"}


def test_a_merge_closes_both_legs_and_is_not_a_worthless_expiry():
    """RN1 merges 2,845 times. Unmodelled, those positions look like they rotted
    to zero — we measured -$14.2M of 'expired worthless' that never happened."""
    rows = [trade(1000, "BUY", 100, 30.0, asset=TOK_YES),
            trade(1000, "BUY", 100, 60.0, asset=TOK_NO, outcome="No"),
            merge(2000, 100, 100.0)]
    lots, flows = wa.build_lots(rows, {CID: MARKET})
    merged = [l for l in lots if l.exit_kind == "merge"]
    assert len(merged) == 2
    assert flows["merges"] == 1
    # Paid $90 for a bundle worth $100.
    assert sum(l.pnl for l in merged) == pytest.approx(10.0)
    assert not [l for l in lots if l.exit_kind == "resolved"]


def test_the_merge_payout_is_split_in_proportion_to_what_each_leg_cost():
    """Total P&L is 1 - Σentry however the dollar is split; only the per-band
    attribution depends on the convention. Cost-proportional leaves a
    break-even bundle break-even on BOTH legs instead of inventing a winner
    and a loser."""
    rows = [trade(1000, "BUY", 100, 30.0, asset=TOK_YES),
            trade(1000, "BUY", 100, 70.0, asset=TOK_NO, outcome="No"),
            merge(2000, 100, 100.0)]
    lots, _ = wa.build_lots(rows, {CID: MARKET})
    for lot in lots:
        assert lot.pnl == pytest.approx(0.0)
        assert lot.exit_price == pytest.approx(lot.entry_price)


def test_a_merge_with_no_token_map_is_reported_not_guessed():
    rows = [trade(1000, "BUY", 100, 30.0), merge(2000, 100, 100.0)]
    lots, flows = wa.build_lots(rows, {})
    assert flows["unhandled"].get("MERGE (no token map)") == 1
    assert not [l for l in lots if l.exit_kind == "merge"]


def test_split_is_left_unmodelled_and_surfaced():
    """A SPLIT's cost per leg is genuinely unknown, and any convention we pick
    would silently distort the entry-price bands — which are the centrepiece of
    the report. Better counted and named than guessed at."""
    rows = [trade(1000, "BUY", 100, 30.0),
            {"timestamp": 1500, "type": "SPLIT", "conditionId": CID, "size": 50,
             "usdcSize": 50, "transactionHash": "0x1"}]
    _, flows = wa.build_lots(rows, {CID: MARKET})
    assert flows["unhandled"].get("SPLIT") == 1
