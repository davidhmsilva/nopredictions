-- 056_paper_trade_provisional.sql — a result we can see before the venue says so.
--
-- A self-settling agent (nfl_agent) waits for Polymarket's `winner` flag on the
-- CLOB. Measured over the first 15 settled NFL trades, that flag arrives
-- 3.25h-6.0h after kick-off (median 4.9h) — and our own settle gate opens at
-- 3.0h, earlier than the flag on 15 of 15. So there is always a window, often
-- hours long, where the game is FINAL, the price already reads 0.996, and the
-- record still says OPEN because nothing has resolved.
--
-- These columns hold the outcome graded from the SCORE (ESPN, STATUS_FINAL
-- only) while `result` stays NULL until the venue resolves. They are a display
-- state, never a settlement:
--   * `result` / `payout_units` are untouched, so no yield, CLV or report
--     number moves because of anything written here;
--   * when the venue does resolve, `provisional_result` is kept beside it, so
--     "did our grading agree with the venue" is answerable from the data —
--     the rule_correct arm that db/039 exists for, free on every trade.
--
-- ⚠️ The game state, not a clock, is what gates this. On 2026-09-20 the 3h gate
-- had already passed on a 17-17 game heading to overtime and on a suspended
-- one; neither had an outcome to grade.
--
-- Grants: paper_trades is read by the site over DATABASE_URL only (db/050), and
-- anon/authenticated hold no privilege on it (db/048). A new column inherits
-- that, so nothing is granted here.

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS provisional_result text,
    ADD COLUMN IF NOT EXISTS provisional_detail text,
    ADD COLUMN IF NOT EXISTS provisional_source text,
    ADD COLUMN IF NOT EXISTS provisional_at     timestamptz;

ALTER TABLE paper_trades
    DROP CONSTRAINT IF EXISTS paper_trades_provisional_result_chk;
ALTER TABLE paper_trades
    ADD CONSTRAINT paper_trades_provisional_result_chk
    CHECK (provisional_result IS NULL OR provisional_result IN ('won', 'lost', 'void'));

COMMENT ON COLUMN paper_trades.provisional_result IS
    'Outcome graded from the final score while the venue has not resolved. Display only — never settles a trade, never enters a yield.';
COMMENT ON COLUMN paper_trades.provisional_detail IS
    'Human-readable basis, e.g. "Vikings 9 @ Bears 3 — total 12 < 40.5".';
COMMENT ON COLUMN paper_trades.provisional_source IS
    'Where the score came from, e.g. espn_final.';
