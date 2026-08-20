-- 032_pressure_goal_minute.sql
--
-- The site shows Live Pressure Overs entries. Two things it could not show:
-- the minute the bet was made, and — when the bet won — the minute the goal
-- arrived. Neither existed in a form the site could reach:
--
--   * the entry minute lives on pressure_observations.minute, and paper_trades
--     has no minute column at all;
--   * the goal minute was never recorded anywhere. settle() stored only
--     WHETHER a goal came (goal_next_10 / goal_before_ft), never WHEN.
--
-- So: store the goal minute at settle time, and expose both through one view
-- keyed by paper_trade_id, rather than opening pressure_observations (60+
-- columns of in-progress research) to the anon key.

ALTER TABLE pressure_observations
  ADD COLUMN IF NOT EXISTS goal_minute        int,
  ADD COLUMN IF NOT EXISTS goal_minute_source text;

COMMENT ON COLUMN pressure_observations.goal_minute IS
  'Minute of the first goal after entry, for entered rows that won. NULL when '
  'the bet lost (no goal) or is unsettled.';
COMMENT ON COLUMN pressure_observations.goal_minute_source IS
  '"api" = api-football /fixtures/events, exact including stoppage time. '
  '"poll" = the minute WE first observed the new score, which is our 60s '
  'polling cadence late and caps at 90 for stoppage-time goals. Never present '
  'the two as equally precise.';

-- One row per Live Pressure Overs entry, for the public site.
CREATE OR REPLACE VIEW v_pressure_trades AS
SELECT
    o.paper_trade_id,
    o.fixture_id,
    o.obs_version,
    o.home,
    o.away,
    o.league,
    o.minute            AS entry_minute,
    o.goals_total       AS goals_at_entry,
    o.target_line,
    o.best_ask          AS entry_price,
    o.pressure_index,
    o.goal_minute,
    o.goal_minute_source,
    o.goal_before_ft    AS won,
    o.final_goals,
    o.observed_at       AS entered_at
FROM pressure_observations o
WHERE o.entered AND o.paper_trade_id IS NOT NULL;

COMMENT ON VIEW v_pressure_trades IS
  'Public projection of Live Pressure Overs entries. Join to paper_trades on '
  'paper_trade_id = paper_trades.id.';

GRANT SELECT ON v_pressure_trades TO anon, authenticated;
