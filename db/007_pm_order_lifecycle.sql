-- Track order lifecycle: attempts, last refresh, and the actual time the order
-- was first submitted to PM (distinct from paper_trades.placed_at which is when
-- the edge was originally detected).

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS pm_attempts          INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pm_executed_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS pm_last_checked_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS pm_size_matched      NUMERIC(16, 4) DEFAULT 0;

-- Backfill pm_executed_at = NOW() for the live trades we already placed
UPDATE paper_trades
SET pm_executed_at = COALESCE(pm_executed_at, NOW()),
    pm_attempts    = GREATEST(pm_attempts, 1)
WHERE pm_live = TRUE
  AND pm_order_id IS NOT NULL;

COMMENT ON COLUMN paper_trades.pm_attempts IS
  'How many times we have submitted an order for this trade. Capped by retry logic.';
COMMENT ON COLUMN paper_trades.pm_executed_at IS
  'When the latest order was successfully submitted to PM (vs placed_at = when the edge was first detected).';
