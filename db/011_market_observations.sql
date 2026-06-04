-- 011_market_observations.sql
-- Observation layer: a continuous time series of model fair value vs Polymarket
-- price for EVERY market we can price — not just the ones that become trades.
--
-- This is the dataset that answers "where and when does edge actually live?":
--   * edge trajectory vs time-to-kickoff (does our edge grow or decay near KO?)
--   * after settlement, did PM converge to our model (we were right) or away (wrong)?
--   * which leagues / market groups / time buckets carry persistent edge.
--
-- No money, no threshold. Pure observation, written every ~30 min by observer.py.

CREATE TABLE IF NOT EXISTS market_observations (
    id                 BIGSERIAL PRIMARY KEY,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    model              TEXT        NOT NULL DEFAULT 'sim',  -- fair-value source

    home               TEXT,
    away               TEXT,
    pm_external_id     TEXT        NOT NULL,                -- PM market id
    pm_token_id        TEXT,                               -- YES token
    question           TEXT,
    outcome_key        TEXT        NOT NULL,               -- home_win, over_2_5, ...
    market_group       TEXT,                               -- 1x2/halftime/totals/btts/handicap

    model_prob         NUMERIC     NOT NULL,               -- our fair prob for YES
    pm_yes             NUMERIC     NOT NULL,               -- PM mid (outcomePrices[0])
    pm_bid             NUMERIC,                            -- bestBid
    pm_ask             NUMERIC,                            -- bestAsk
    edge_pp            NUMERIC     NOT NULL,               -- (model_prob - pm_yes) * 100

    volume             NUMERIC,
    liquidity          NUMERIC,
    kickoff_utc        TIMESTAMPTZ,
    minutes_to_kickoff NUMERIC                             -- + = pre-match, - = in-play
);

-- Trajectory of one market/outcome over time.
CREATE INDEX IF NOT EXISTS idx_obs_market_outcome_time
    ON market_observations (pm_external_id, outcome_key, observed_at);
-- All observations for a match.
CREATE INDEX IF NOT EXISTS idx_obs_match
    ON market_observations (home, away, observed_at);
-- Recent-snapshot scans.
CREATE INDEX IF NOT EXISTS idx_obs_time
    ON market_observations (observed_at);

COMMENT ON TABLE market_observations IS
    'Continuous model-vs-PM-price time series (observation layer). No trades. Written by observer.py.';
