-- 010_clv_source.sql
-- Make CLV trustworthy at the source.
--
-- Problem (found 2026-05-28 via agent/evaluate.py):
--   * 167/247 clv values were CIRCULAR — resolver fell back to using our own
--     model_probability as the "closing line", so clv compared our entry against
--     our own model. Not edge. Inflated site avg_clv to +56%.
--   * Other rows stored closing_price = 0.0 (impossible probability) yet computed
--     a clv from mis-extracted longshot odds → fake clv of +700%.
--
-- Fix:
--   * `clv`        — now holds ONLY real market closing-line CLV (sharp_closing /
--                    pinnacle_fd), validated 0 < closing_price < 1 and |clv| <= 0.5.
--   * `model_clv`  — diagnostic: entry vs our own model line. Never confused with clv.
--   * `clv_source` — provenance: sharp_closing | pinnacle_fd | model | suspect | artifact.
--
-- After this, v_strategy_performance.avg_clv (AVG(clv) FILTER clv IS NOT NULL)
-- automatically reflects only real CLV — no view change needed.

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS clv_source TEXT,
    ADD COLUMN IF NOT EXISTS model_clv  NUMERIC;

-- ── Backfill / clean existing rows (order matters) ──────────────────────────

-- (a) CIRCULAR: closing line is just our own model echoed back.
--     Move the value to model_clv, blank the fake closing line + clv.
UPDATE paper_trades
SET model_clv     = clv,
    clv           = NULL,
    closing_price = NULL,
    clv_source    = 'model'
WHERE clv IS NOT NULL
  AND closing_price IS NOT NULL
  AND model_probability IS NOT NULL
  AND ABS(closing_price - model_probability) < 1e-4;

-- (b) ARTIFACT: impossible closing probability (<=0 or >=1).
UPDATE paper_trades
SET clv           = NULL,
    closing_price = NULL,
    clv_source    = 'artifact'
WHERE clv IS NOT NULL
  AND (closing_price IS NULL OR closing_price <= 0 OR closing_price >= 1);

-- (c) SUSPECT: closing prob looks valid but |clv| is implausibly large
--     (>50%) — almost always bad outcome-mapping / longshot extraction noise.
UPDATE paper_trades
SET clv        = NULL,
    clv_source = 'suspect'
WHERE clv IS NOT NULL
  AND ABS(clv) > 0.5;

-- (d) REAL: whatever survives is a trustworthy market CLV. Tag provenance.
UPDATE paper_trades
SET clv_source = CASE
        WHEN closing_sharp_odds IS NOT NULL THEN 'sharp_closing'
        ELSE 'pinnacle_fd'
    END
WHERE clv IS NOT NULL
  AND clv_source IS NULL;

COMMENT ON COLUMN paper_trades.clv IS
    'Real market closing-line value only (entry_odds*closing_prob - 1). NULL unless a sharp/pinnacle closing line was available and plausible. See clv_source.';
COMMENT ON COLUMN paper_trades.model_clv IS
    'Diagnostic: entry odds vs our own model line. NOT real CLV — do not aggregate as edge.';
COMMENT ON COLUMN paper_trades.clv_source IS
    'sharp_closing | pinnacle_fd | model | suspect | artifact';
