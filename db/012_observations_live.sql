-- 012_observations_live.sql
-- Extend the observation layer to in-play. Same table, plus live match context so
-- we can measure edge vs match minute / score — the core of the "live is more
-- inefficient, especially post-70'" thesis. Pre-match rows leave these NULL.

ALTER TABLE market_observations
    ADD COLUMN IF NOT EXISTS phase      TEXT NOT NULL DEFAULT 'pre',  -- 'pre' | 'live'
    ADD COLUMN IF NOT EXISTS live_minute INT,
    ADD COLUMN IF NOT EXISTS live_score  TEXT,   -- 'home-away', e.g. '1-0'
    ADD COLUMN IF NOT EXISTS home_reds   INT,
    ADD COLUMN IF NOT EXISTS away_reds   INT;

-- Edge-vs-minute slicing for in-play analysis.
CREATE INDEX IF NOT EXISTS idx_obs_phase_minute
    ON market_observations (phase, live_minute);

COMMENT ON COLUMN market_observations.phase IS 'pre = pre-match snapshot, live = in-play snapshot';
COMMENT ON COLUMN market_observations.live_minute IS 'match minute at observation (in-play only)';
