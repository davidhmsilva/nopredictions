-- 013_observations_refined_edge.sql
-- Shadow mode for the refined (sharp-anchored + uncertainty) edge engine.
-- The observation layer already records every market's model_prob / pm price /
-- naive edge_pp. Here we add the REFINED edge alongside, so we can compare
-- naive-vs-refined against real outcomes WITHOUT touching the betting path.
--
-- After matches settle we join these against results to answer: do the markets
-- the refined engine would bet convert better than the naive ones? do the ones
-- it kills (sharp-contradicted / model-only delusions) indeed lose?

ALTER TABLE market_observations
    ADD COLUMN IF NOT EXISTS sharp_prob       NUMERIC,   -- de-vigged Pinnacle, NULL if none
    ADD COLUMN IF NOT EXISTS edge_refined_pp  NUMERIC,   -- adjusted edge from edge_engine
    ADD COLUMN IF NOT EXISTS edge_confidence  TEXT,      -- 'sharp' | 'model'
    ADD COLUMN IF NOT EXISTS refined_bet_ok   BOOLEAN;   -- would the refined engine bet?

CREATE INDEX IF NOT EXISTS idx_obs_refined
    ON market_observations (refined_bet_ok, edge_confidence);

COMMENT ON COLUMN market_observations.edge_refined_pp IS
    'edge_engine output: sharp-anchored consensus − executable price, minus uncertainty haircut';
COMMENT ON COLUMN market_observations.refined_bet_ok IS
    'shadow: whether the refined engine would have bet this market (no money placed)';
