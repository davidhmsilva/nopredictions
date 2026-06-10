-- 018_convergence_post_goal.sql
-- Mark entries that were triggered by a detected goal event, so we can
-- compare post-goal entries vs regular entries in the backtest.

ALTER TABLE convergence_shadow
    ADD COLUMN IF NOT EXISTS post_goal            BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pre_goal_score       TEXT,    -- score just before the goal, e.g. "0-1"
    ADD COLUMN IF NOT EXISTS goal_detected_minute INT;     -- live minute when goal change was detected
