-- Track real Polymarket order execution alongside paper records.
-- When PM_LIVE_MODE=1, the live_executor submits the order to CLOB and stores
-- the resulting order_id + token + filled amounts here. NULL means paper-only.

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS pm_token_id     TEXT,
  ADD COLUMN IF NOT EXISTS pm_order_id     TEXT,
  ADD COLUMN IF NOT EXISTS pm_live         BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS pm_order_size   NUMERIC(16, 4),
  ADD COLUMN IF NOT EXISTS pm_order_price  NUMERIC(10, 6),
  ADD COLUMN IF NOT EXISTS pm_order_status TEXT,
  ADD COLUMN IF NOT EXISTS pm_order_error  TEXT;

CREATE INDEX IF NOT EXISTS idx_paper_trades_pm_order_id
  ON paper_trades (pm_order_id)
  WHERE pm_order_id IS NOT NULL;

COMMENT ON COLUMN paper_trades.pm_order_id IS
  'Polymarket CLOB order id. NULL when paper-only (PM_LIVE_MODE=0).';
COMMENT ON COLUMN paper_trades.pm_order_status IS
  'matched | live | partial | cancelled | failed';
