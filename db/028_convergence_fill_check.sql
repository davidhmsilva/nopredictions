-- 028 — convergence: separate the exit SIGNAL from the exit FILL
--
-- Why: _close_position() booked realized_pnl_usd at the quoted top-of-book bid
-- with no evidence the size was ever there. Paired test on the 15 real-money
-- rows (2026-07-22): paper +141.0% vs real +3.4% on identical decisions — a
-- 137.6pp gap. Of the 4 live exits that actually filled, fills landed at
-- 0.09-0.27 against a much higher quoted bid; 11 never sold at all yet 4 of
-- those were still booked as "converged" wins, 3 of which then lost at
-- settlement.
--
-- From here: realized_pnl_usd is only written on a VERIFIED fill — a matched
-- live SELL, or a CLOB bid ladder that genuinely holds size_shares. The quoted
-- bid that the old code would have booked is preserved below so the
-- phantom-exit gap stays measurable instead of silently disappearing.

ALTER TABLE convergence_shadow
    ADD COLUMN IF NOT EXISTS exit_signal_minute  integer,
    ADD COLUMN IF NOT EXISTS exit_signal_bid     numeric,
    ADD COLUMN IF NOT EXISTS exit_fill_verified  boolean;

COMMENT ON COLUMN convergence_shadow.exit_signal_minute IS
    'First minute the convergence condition (fair - bid <= EXIT_BUFFER_PP) fired.';
COMMENT ON COLUMN convergence_shadow.exit_signal_bid IS
    'Quoted best bid at that moment — counterfactual only. This is what the '
    'pre-028 code booked as realized P&L. NOT evidence of an executable price.';
COMMENT ON COLUMN convergence_shadow.exit_fill_verified IS
    'TRUE only when the exit price came from a matched live SELL or a CLOB bid '
    'ladder with real depth for the full position. Rows closed before 028 are '
    'NULL — treat their realized_pnl_usd as unverified.';

-- Existing rows: their exits were never fill-checked. Mark them so the report
-- and any future analysis cannot mistake them for executable results.
UPDATE convergence_shadow
   SET exit_fill_verified = FALSE
 WHERE status = 'closed'
   AND exit_fill_verified IS NULL
   AND exit_reason = 'converged';

-- Settlement redemptions ARE real fills (shares redeem at $1/$0 on resolution).
UPDATE convergence_shadow
   SET exit_fill_verified = TRUE
 WHERE status = 'closed'
   AND exit_fill_verified IS NULL
   AND exit_reason IN ('settled_win', 'settled_loss');


-- ── Second defect: sibling markets settled against the full-time result ──────
--
-- An older _classify_market mapped "X to win the second half?" and
-- "A vs. B: Second half draw?" onto plain home_win / away_win / draw, so 14
-- second-half markets entered the shadow ledger and were then resolved by
-- _fetch_final_result — the FULL-TIME score. Their settle_result, settle_pnl_usd
-- and realized_pnl_usd are all resolved off the wrong event. The current
-- classifier rejects these, and the shadow entry path now applies the same
-- _NOT_1X2_RE screen the live path uses, so no new rows can be affected.
--
-- Flagged rather than deleted — the price paths are still real observations.
-- Exclude market_is_1x2 = FALSE from any P&L or edge analysis.

ALTER TABLE convergence_shadow
    ADD COLUMN IF NOT EXISTS market_is_1x2 boolean NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN convergence_shadow.market_is_1x2 IS
    'FALSE = sibling market (second half, score-first, O/U) that reached the '
    'ledger through an older classifier and was settled against the full-time '
    'result. P&L is not trustworthy — exclude from analysis.';

UPDATE convergence_shadow
   SET market_is_1x2 = FALSE
 WHERE question ~* '\m(second half|neither team|o/u|over|under|to score first)\M';
