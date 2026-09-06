-- 030 — late-goal observer: clock verification, book confirmation, series break
--
-- WHY THIS EXISTS
-- ---------------
-- The first 13 days of `late_goal_observations` (2026-07-22 → 2026-08-04,
-- 37,297 live polls) cannot answer the question the table was built for. Two
-- independent defects, both found on 2026-08-04:
--
-- 1. THE CLOCK. game_minute came from PM's gameStartTime with a flat 15-minute
--    halftime and nothing checking it. On a sample of 26 settled fixtures the
--    true full-time total (recovered from which over lines resolved YES on the
--    CLOB) averaged 2.65 against 1.69 at what we called minute 90 — i.e. an
--    average of 0.96 goals arrived AFTER we stopped watching. At ~0.03 goals
--    per real minute that is ~30 minutes of match we never saw. It is not
--    universal: Man Utd 2-1 Atletico (2026-08-01, KO 13:00 UTC, goals 5'/53'/74')
--    was clocked and scored correctly end to end. It is a subset — smaller
--    leagues, where PM's listed start time is not the real kick-off.
--    Consequence: lgt.lookup() was handed a minute far later than reality, the
--    fair value came out far too low, and PM sat above it on 4,862 of 4,916
--    eligible polls. That 99% "no edge" reading is our own clock, not a price.
--
-- 2. THE SCORE. infer_goals() reads Gamma's outcomePrices. Where api-football
--    answered (1,048 polls) the ladder DISAGREED on 305 of them — 29% — and in
--    181 of those the API saw MORE goals than the ladder. The existing
--    cross-check downgrades those rows to certain=false, so the surviving
--    "certain" set looks perfect by construction (743/743). On the other 94% of
--    polls the API never answers and an undercounting ladder passes unchallenged.
--    Gamma also lags the CLOB: on 20% of polls the CLOB ask sat >20pp above the
--    Gamma mid on the same token, which is the signature of a line that has
--    already been won while Gamma still quotes it live.
--
-- obs_version splits the series. Everything written before this migration is
-- version 1 and must be excluded from any analysis of the question; version 2
-- carries the clock offset and the book confirmation alongside every row.

ALTER TABLE late_goal_observations
    ADD COLUMN IF NOT EXISTS obs_version      smallint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS api_minute       integer,
    ADD COLUMN IF NOT EXISTS clock_offset_min integer,
    ADD COLUMN IF NOT EXISTS clock_verified   boolean  NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS book_confirmed   boolean  NOT NULL DEFAULT false;

COMMENT ON COLUMN late_goal_observations.obs_version IS
    '1 = pre-2026-08-04 series, clock unverified and score unconfirmed, do not analyse. 2 = current.';
COMMENT ON COLUMN late_goal_observations.api_minute IS
    'api-football status.elapsed at poll time. NULL whenever the daily quota is gone, which is most of the time.';
COMMENT ON COLUMN late_goal_observations.clock_offset_min IS
    'game_minute - api_minute. The size of the gameStartTime error on this fixture.';
COMMENT ON COLUMN late_goal_observations.clock_verified IS
    'api-football corroborated the wall-clock minute to within CLOCK_TOLERANCE_MIN. would_enter requires it.';
COMMENT ON COLUMN late_goal_observations.book_confirmed IS
    'The CLOB books on the two boundary rungs agree with the Gamma ladder, so the inferred score is not a stale quote.';

CREATE INDEX IF NOT EXISTS idx_lgo_version_live
    ON late_goal_observations (obs_version, phase, game_minute);
