-- 053_factory_explore.sql — an `explore` tier for the strategy factory.
--
-- The first grid (2026-09-13, 12,796 specs) passed nothing out of sample, and
-- the in-play tapes are one month deep: a median tested spec had 66 train and
-- 28 test entries, too few to see a 5-10% edge either way. Paper is free and the
-- forward record is the only unbiased test, so the best specs that were positive
-- on train, test AND the calibration arm are paper-traded as `explore`.
--
-- Promotion out of explore/paper is controlled for multiple testing: forward
-- one-sided p-values of every active strategy go through Benjamini-Hochberg, and
-- fwd_q is stored beside them.

ALTER TABLE factory_strategies DROP CONSTRAINT IF EXISTS factory_strategies_status_check;
ALTER TABLE factory_strategies ADD CONSTRAINT factory_strategies_status_check
    CHECK (status IN ('candidate', 'explore', 'paper', 'promoted', 'retired'));
ALTER TABLE factory_strategies ADD COLUMN IF NOT EXISTS fwd_q numeric;
