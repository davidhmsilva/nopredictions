-- 043 — which feed a pressure reading came from.
--
-- From 2026-09-06 a refused api-football poll falls back to ESPN's public
-- scoreboard instead of returning nothing. That is not the same measurement
-- from a different place: ESPN publishes no team xG and no shots inside the
-- box, which is 55% of the danger index's weight. The index renormalises for
-- both (`has_xg`, `has_inside`) so the AXIS is preserved, but two readings taken
-- with different terms are two populations, and pooling them into one yield is
-- the mistake this project has already made twice with obs_version.
--
-- `has_xg` alone cannot carry this: an api-football fixture whose competition
-- publishes no xG and an ESPN fixture both read has_xg=false, and only one of
-- them is also missing the inside-box term.
--
-- Default 'api-football' is right for every row already on these tables — the
-- fallback did not exist when they were written.

ALTER TABLE pressure_observations
    ADD COLUMN IF NOT EXISTS stats_source text NOT NULL DEFAULT 'api-football',
    ADD COLUMN IF NOT EXISTS has_inside   boolean;

ALTER TABLE ht_pressure_observations
    ADD COLUMN IF NOT EXISTS stats_source text NOT NULL DEFAULT 'api-football',
    ADD COLUMN IF NOT EXISTS has_inside   boolean;

ALTER TABLE fav_ht_observations
    ADD COLUMN IF NOT EXISTS stats_source text NOT NULL DEFAULT 'api-football',
    ADD COLUMN IF NOT EXISTS has_inside   boolean;

COMMENT ON COLUMN pressure_observations.stats_source IS
    'api-football | espn. ESPN is the free fallback used when the paid feed '
    'refuses; it carries no team xG and no shots-inside-box, so has_inside is '
    'false on those rows and the danger index drops that term rather than '
    'scoring it zero. Never pool the two in one yield.';

-- Every entry made on the fallback, at a glance. The fallback is a degraded
-- reading by construction, so an arm whose record is mostly ESPN rows is
-- reporting something different from one whose record is not.
CREATE INDEX IF NOT EXISTS idx_pressure_obs_source
    ON pressure_observations (stats_source) WHERE entered;
CREATE INDEX IF NOT EXISTS idx_ht_pressure_obs_source
    ON ht_pressure_observations (stats_source) WHERE entered;
CREATE INDEX IF NOT EXISTS idx_fav_ht_obs_source
    ON fav_ht_observations (stats_source) WHERE entered;
