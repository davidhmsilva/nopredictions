-- 035_pressure_has_xg.sql
--
-- xG is 40% of danger_index and api-football supplies it on roughly half the
-- fixtures it covers. Without a flag, a fixture that api-football never priced
-- for xG is indistinguishable from a genuinely quiet one — both read low.
--
-- Measured on the rows already collected (2026-08-19, 13,938 rows carrying a
-- pressure_index):
--
--     with xG:  mean pressure 29.8, p90 54.0, P(goal within 10min) 0.334
--     no xG:    mean pressure 14.6, p90 23.6, P(goal within 10min) 0.277
--
-- With MIN_PRESSURE = 45 a fixture with no xG essentially cannot enter — its
-- p90 is 23.6. So the entry filter has been selecting "leagues where
-- api-football serves xG" at least as much as "high pressure", and those
-- leagues score more anyway. That confound has to be a column before it can be
-- controlled for; it is exactly the control the first-half arm already carries
-- (db/033).

ALTER TABLE pressure_observations
  ADD COLUMN IF NOT EXISTS has_xg boolean;

COMMENT ON COLUMN pressure_observations.has_xg IS
  'Did api-football supply any xG for this fixture at this poll. NULL on rows '
  'written before 2026-08-19, where it was never recorded — those cannot be '
  'told apart and must be excluded from any fit that controls for it. False '
  'means the index was computed with its 40% xG term at zero.';
