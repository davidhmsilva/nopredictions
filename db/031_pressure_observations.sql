-- 031 — live pressure observations
--
-- One row per (fixture, poll, target line). The point of the table is to make a
-- question answerable that currently is not: does in-game pressure predict the
-- next goal beyond what the clock and the score already tell you?
--
-- Nothing in our history can answer it. `goal_events` has goals at minute
-- resolution for 16,479 matches but no in-game stat series at all — nobody ever
-- recorded shots, xG or possession minute by minute — so a pressure model
-- cannot be backtested. It has to be recorded forward, which is what this is.
--
-- The decomposition is the whole design. Every row carries BOTH fair values:
--   fair_base      — the empirical table alone (minute, goals, pre-match total)
--   fair_pressure  — the same, with the in-game pressure multiplier applied
-- and settlement records both horizons (next 10 minutes, and before full time).
-- With both stored, "did pressure add anything over the base rate" is a
-- regression on this table rather than an opinion, and the pressure gain can be
-- refit later without re-running anything live.

CREATE TABLE IF NOT EXISTS pressure_observations (
    id                  BIGSERIAL PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    obs_version         SMALLINT    NOT NULL DEFAULT 1,

    -- identity: api-football is the clock and score authority here
    fixture_id          BIGINT      NOT NULL,
    league              TEXT,
    home                TEXT        NOT NULL,
    away                TEXT        NOT NULL,
    event_title         TEXT,                   -- Polymarket's title, when matched
    condition_id        TEXT,
    token_id            TEXT,

    minute              SMALLINT    NOT NULL,   -- api-football elapsed
    home_goals          SMALLINT    NOT NULL,
    away_goals          SMALLINT    NOT NULL,
    goals_total         SMALLINT    NOT NULL,
    ladder_goals        SMALLINT,               -- Gamma's implied total, cross-check only
    score_agrees        BOOLEAN,                -- ladder == api; NULL when no ladder

    -- pressure features, current totals
    home_xg             REAL,
    away_xg             REAL,
    home_shots_on       SMALLINT,
    away_shots_on       SMALLINT,
    home_shots_total    SMALLINT,
    away_shots_total    SMALLINT,
    home_shots_inside   SMALLINT,
    away_shots_inside   SMALLINT,
    home_corners        SMALLINT,
    away_corners        SMALLINT,
    home_possession     REAL,
    away_possession     REAL,
    home_reds           SMALLINT,
    away_reds           SMALLINT,

    -- pressure features, deltas over the rolling window
    window_min          SMALLINT,
    home_xg_window      REAL,
    away_xg_window      REAL,
    home_shots_on_window   SMALLINT,
    away_shots_on_window   SMALLINT,
    home_shots_inside_window SMALLINT,
    away_shots_inside_window SMALLINT,
    home_corners_window SMALLINT,
    away_corners_window SMALLINT,
    has_window          BOOLEAN NOT NULL DEFAULT false,  -- false = scaled fallback, not a real delta

    -- composite
    home_danger         REAL,
    away_danger         REAL,
    pressure_index      REAL,        -- the scalar the multiplier is derived from
    pressure_factor     REAL,        -- k applied to the baseline goal rate

    -- pricing
    pre_over25          REAL,
    target_line         REAL,
    best_bid            REAL,
    best_ask            REAL,
    bid_depth_usd       REAL,
    ask_depth_usd       REAL,
    fair_base           REAL,        -- empirical table, no pressure term
    fair_pressure       REAL,        -- with the pressure term
    fair_n              INTEGER,
    fee_pp              REAL,
    edge_base_pp        REAL,
    edge_pressure_pp    REAL,
    would_enter         BOOLEAN NOT NULL DEFAULT false,
    entered             BOOLEAN NOT NULL DEFAULT false,
    paper_trade_id      INTEGER REFERENCES paper_trades(id),
    skip_reason         TEXT,        -- why an edge did not become a trade

    -- settlement, both horizons
    goals_at_plus_10    SMALLINT,
    goal_next_10        BOOLEAN,
    final_goals         SMALLINT,
    goal_before_ft      BOOLEAN,
    settled_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_press_obs_fixture ON pressure_observations (fixture_id, minute);
CREATE INDEX IF NOT EXISTS idx_press_obs_observed ON pressure_observations (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_press_obs_unsettled ON pressure_observations (settled_at)
    WHERE settled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_press_obs_entered ON pressure_observations (entered)
    WHERE entered;

-- One paper entry per fixture per line. Pressure persists across polls, so
-- without this the agent re-buys the same line every 60 seconds while a team
-- camps in the box and the "strategy" becomes a stake-size artifact.
CREATE UNIQUE INDEX IF NOT EXISTS idx_press_obs_one_entry
    ON pressure_observations (fixture_id, target_line) WHERE entered;

-- Pre-registration. The prediction is stated before any row exists, because the
-- tempting failure here is to record for three months and then go looking for
-- whichever pressure threshold happens to have paid.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE — in-game pressure predicts the next goal beyond the base rate',
  'On a live football fixture, buying PM Over(current total + 0.5) when the '
  'rolling-window pressure index is high pays more than buying the same line at '
  'the same minute and score without the pressure filter. '
  'PRE-REGISTERED PREDICTION: on >= 200 settled entries at obs_version 1, the '
  'pressure arm beats the base-table arm on yield, and its yield CI excludes '
  'zero after the taker fee. '
  'FALSIFIED IF: the two arms are within noise of each other — that would mean '
  'pressure is already in PM''s price, which is the null and the likely outcome. '
  'PRIMARY TEST: logistic regression of goal_next_10 on pressure_index with '
  'minute, goals and pre_over25 as controls, over ALL observation rows (entered '
  'or not). The yield comparison is secondary — it has far less power.',
  'The market prices the clock and the score, both of which are public and '
  'instant. Shots, xG and territory arrive on a slower feed and are harder to '
  'aggregate, so if anything in-play is mispriced it is more likely to be this '
  'than the score. Against that: PM in-play mid is a martingale on 298k '
  'observations, and every in-play edge we have measured so far died at the '
  'spread.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE%');

INSERT INTO strategies (hypothesis_id, name, rules)
SELECT (SELECT id FROM research_hypotheses WHERE title LIKE 'H-PRESSURE%' LIMIT 1),
       'Live Pressure Overs',
       '{"venue":"polymarket","market":"over N.5 full match","phase":"in-play",'
       '"signal":"api-football in-game pressure vs empirical base rate",'
       '"status":"paper only — pressure multiplier is UNFITTED, recording to fit it"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Live Pressure Overs');
