-- 019_convergence_live.sql
-- Add real-money execution columns to convergence_shadow.
-- Entry: pm_live=TRUE when a live BUY was submitted on Polymarket.
-- Exit:  pm_exit_* filled when a live SELL is submitted on convergence.

ALTER TABLE convergence_shadow
    ADD COLUMN IF NOT EXISTS pm_live              BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pm_order_id_entry    TEXT,        -- CLOB order id from BUY
    ADD COLUMN IF NOT EXISTS pm_live_size         NUMERIC,     -- shares actually bought
    ADD COLUMN IF NOT EXISTS pm_live_stake_usd    NUMERIC,     -- USD staked (CONV_LIVE_STAKE_USD)
    ADD COLUMN IF NOT EXISTS pm_exit_order_id     TEXT,        -- CLOB order id from SELL
    ADD COLUMN IF NOT EXISTS pm_exit_price_actual NUMERIC,     -- sell price executed
    ADD COLUMN IF NOT EXISTS pm_live_pnl_usd      NUMERIC;     -- realized $ on real position
