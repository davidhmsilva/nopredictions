-- 016_convergence_path.sql
-- Richer measurement for the convergence shadow ledger, so we can answer the
-- open question EMPIRICALLY: exit on convergence vs hold to resolution vs exit
-- at the peak. For every entry we record (a) the full price path while the match
-- is live, and (b) the exact final settlement — REGARDLESS of when/if the
-- strategy's converged-exit fired. Offline we can then replay any exit rule.

-- (a) Per-cycle price path for each shadow position.
CREATE TABLE IF NOT EXISTS convergence_path (
    id           BIGSERIAL PRIMARY KEY,
    position_id  BIGINT NOT NULL REFERENCES convergence_shadow(id) ON DELETE CASCADE,
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    minute       INT,
    fair         NUMERIC,    -- recomputed sim fair this cycle
    bid          NUMERIC,    -- PM best bid (what we'd sell into)
    ask          NUMERIC     -- PM best ask
);
CREATE INDEX IF NOT EXISTS convergence_path_pos ON convergence_path (position_id);

-- (b) Hold-to-resolution counterfactual + peak, on the entry row itself.
-- These are filled for EVERY entry, independent of the strategy's exit.
ALTER TABLE convergence_shadow
    ADD COLUMN IF NOT EXISTS settle_result   TEXT,     -- draw / home_win / away_win at FT
    ADD COLUMN IF NOT EXISTS settle_price    NUMERIC,  -- 1.0 if our token won else 0.0
    ADD COLUMN IF NOT EXISTS settle_pnl_usd  NUMERIC,  -- hold-to-end P&L on the entry
    ADD COLUMN IF NOT EXISTS peak_bid        NUMERIC,  -- best bid seen post-entry
    ADD COLUMN IF NOT EXISTS peak_minute     INT,
    ADD COLUMN IF NOT EXISTS settled_at      TIMESTAMPTZ;  -- when settle_* was filled
