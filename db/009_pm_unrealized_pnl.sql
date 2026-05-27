-- 009: Per-trade unrealized P&L snapshot fields.
--
-- Populated by pm_chain_sync.py from PM data API (cashPnl, currentValue,
-- percentPnl). These are mark-to-market values reflecting the gap between
-- avgPrice paid and curPrice right now — i.e. open P&L on resting + matched
-- positions before the bet resolves.

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS pm_current_value  numeric(18, 6),
    ADD COLUMN IF NOT EXISTS pm_cash_pnl       numeric(18, 6),
    ADD COLUMN IF NOT EXISTS pm_percent_pnl    numeric(10, 4);

COMMENT ON COLUMN paper_trades.pm_current_value IS
  'PM data API currentValue = size × curPrice. Refreshed every sync cycle.';
COMMENT ON COLUMN paper_trades.pm_cash_pnl IS
  'PM data API cashPnl = currentValue − initialValue. Unrealized P&L on this position.';
COMMENT ON COLUMN paper_trades.pm_percent_pnl IS
  'PM data API percentPnl = cashPnl / initialValue × 100.';
