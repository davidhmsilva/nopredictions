-- 033 — first-half pressure observations (Live Pressure HT Over 0.5)
--
-- The sibling of 031, one market over: PM's "1st Half O/U 0.5" bought while the
-- score is still 0-0, on the strength of how the first 15 minutes were played.
--
-- Why a separate table and not a column on pressure_observations: the state that
-- defines a row is different (0-0 in the first half, not "N goals at minute m"),
-- the measurement is different (cumulative pressure scaled to a 15-minute rate,
-- frozen at 15-18', not a rolling delta), and the outcome is different (a goal
-- before the break, settled off api-football's event list). Mixing them would
-- put two populations in one regression, which is exactly what obs_version was
-- invented to prevent on the sibling.
--
-- The decomposition is kept: every row carries the fair value from the empirical
-- table with and without the pressure term, so "did pressure add anything over
-- the base rate" stays a regression on this table rather than an opinion.

CREATE TABLE IF NOT EXISTS ht_pressure_observations (
    id                  BIGSERIAL PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    obs_version         SMALLINT    NOT NULL DEFAULT 1,

    -- identity: api-football is the clock and score authority here
    fixture_id          BIGINT      NOT NULL,
    league              TEXT,
    home                TEXT        NOT NULL,
    away                TEXT        NOT NULL,
    event_title         TEXT,
    condition_id        TEXT,
    token_id            TEXT,

    minute              SMALLINT    NOT NULL,
    home_goals          SMALLINT    NOT NULL,
    away_goals          SMALLINT    NOT NULL,
    goals_total         SMALLINT    NOT NULL,
    ladder_goals        SMALLINT,               -- Gamma's implied total, cross-check only
    score_agrees        BOOLEAN,                -- ladder == api; NULL when no ladder

    -- pressure features, cumulative totals at this poll
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
    -- false = api-football has no stats for this competition, so every feature
    -- above is zero and the index collapses to its possession term. A row like
    -- that is not a quiet match and must never be read as one.
    has_stats           BOOLEAN NOT NULL DEFAULT false,
    -- xG is 40% of the index and api-football supplies it on only about half the
    -- fixtures it covers at all. A fixture without it reads far quieter than it
    -- played, so this has to be a control in any fit — not a silent difference
    -- between two rows that look identical.
    has_xg              BOOLEAN NOT NULL DEFAULT false,

    -- composite
    home_danger         REAL,
    away_danger         REAL,
    pressure_index      REAL,        -- this poll, cumulative, scaled to 15 minutes
    opening_pressure    REAL,        -- the FROZEN 15-18' reading — the signal traded on
    opening_minute      SMALLINT,    -- the minute that reading was taken at
    pressure_factor     REAL,        -- k applied to the baseline goal rate

    -- pricing
    pre_over25          REAL,
    -- false = the over-2.5 mid was first seen after kickoff, so it has already
    -- drifted with the goalless clock and is NOT the pre-match total. The fair
    -- value then falls back to the pooled cell rather than a bucket this price
    -- would have pushed it into.
    pre_is_prematch     BOOLEAN NOT NULL DEFAULT false,
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
    skip_reason         TEXT,

    -- settlement
    ht_goals            SMALLINT,
    goal_before_ht      BOOLEAN,
    goal_minute         SMALLINT,
    goal_minute_source  TEXT,        -- 'api' = exact incl. 45+X, 'poll' = our 60s tape
    settled_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ht_press_fixture ON ht_pressure_observations (fixture_id, minute);
CREATE INDEX IF NOT EXISTS idx_ht_press_observed ON ht_pressure_observations (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ht_press_unsettled ON ht_pressure_observations (settled_at)
    WHERE settled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ht_press_entered ON ht_pressure_observations (entered)
    WHERE entered;

-- One paper entry per fixture. There is a single line in this market, so a
-- second entry while the pressure persists would be a stake-size artifact, not
-- a second decision.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ht_press_one_entry
    ON ht_pressure_observations (fixture_id) WHERE entered;

-- Pre-registration, before any row exists.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE-1H — a high-pressure goalless opening beats the first-half over price',
  'On a live football fixture still 0-0 after 15 minutes, buying PM "1st Half '
  'O/U 0.5" (over) when the pressure index over those first 15 minutes is high '
  'pays more than the market price implies. '
  'PRE-REGISTERED PREDICTION: on >= 200 settled entries at obs_version 1, the '
  'yield CI excludes zero after the taker fee, and the high-pressure arm beats '
  'the base-table arm recorded on the same rows. '
  'FALSIFIED IF: the two arms are within noise, or the yield CI contains zero — '
  'which is the null and, on the evidence below, the likely outcome. '
  'PRIMARY TEST: logistic regression of goal_before_ht on opening_pressure with '
  'minute and pre_over25 as controls, over ALL observation rows (entered or '
  'not), one row per fixture. The yield comparison is secondary — far less power.',
  'This starts from behind and that is the point of pre-registering it. PM''s '
  'price on this exact market sat ABOVE the realised frequency at every minute '
  'tested from 5'' to 40'' (n=236 fixtures reconstructed from the CLOB, gap ~4pp, '
  'CI crossing zero) — so the generic over is rich, and a pressure filter has to '
  'beat ~4pp plus fee plus spread before it is worth anything. The case for '
  'testing it anyway: the clock and the score are public and instant, while '
  'shots, xG and territory arrive on a slower feed and are harder to aggregate, '
  'so if anything in-play is mispriced it is more likely to be this. Against it: '
  'liquidity on this line is micro ($938 seen against $32,718 on the same '
  'fixture''s full-match O/U 2.5), and every in-play edge measured here so far '
  'has died at the spread.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-1H%');

INSERT INTO strategies (hypothesis_id, name, rules)
SELECT (SELECT id FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-1H%' LIMIT 1),
       'Live Pressure HT Over 0.5',
       '{"venue":"polymarket","market":"1st half over 0.5","phase":"in-play",'
       '"entry":"0-0 at 15-25 minutes with a high-pressure opening 15",'
       '"signal":"api-football in-game pressure, frozen at 15-18 minutes",'
       '"status":"paper only — pressure threshold is UNFITTED, recording to fit it"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Live Pressure HT Over 0.5');

-- Public projection for the site, column-compatible with v_pressure_trades so
-- the dashboard can read both agents through one shape. target_line is the
-- constant 0.5 here; goals_at_entry is always 0 by construction.
CREATE OR REPLACE VIEW v_ht_pressure_trades AS
SELECT
    o.paper_trade_id,
    o.fixture_id,
    o.obs_version,
    o.home,
    o.away,
    o.league,
    o.minute            AS entry_minute,
    o.goals_total       AS goals_at_entry,
    0.5::real           AS target_line,
    o.best_ask          AS entry_price,
    o.opening_pressure  AS pressure_index,
    o.goal_minute,
    o.goal_minute_source,
    o.goal_before_ht    AS won,
    o.ht_goals          AS final_goals,
    o.observed_at       AS entered_at
FROM ht_pressure_observations o
WHERE o.entered AND o.paper_trade_id IS NOT NULL;

COMMENT ON VIEW v_ht_pressure_trades IS
  'Public projection of Live Pressure HT Over 0.5 entries. Same columns as '
  'v_pressure_trades. Join to paper_trades on paper_trade_id = paper_trades.id.';

GRANT SELECT ON v_ht_pressure_trades TO anon, authenticated;
