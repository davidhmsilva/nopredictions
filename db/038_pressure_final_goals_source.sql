-- 038_pressure_final_goals_source.sql
--
-- Strategy 16 settled every entry off the MAXIMUM goal total our own poll tape
-- ever recorded for the fixture. That is not a final score.
--
-- The api-football live score flaps. Aberdeen 0-1 Rangers (fixture 1556646,
-- 2026-08-30) read 0-1 up to 82', then 1-1 for three polls at 83-85', then 0-1
-- again from 86' to the end. The match finished 0-1 — one goal, Shankland 69'.
-- `max(tape)` returned 2, so `goal_before_ft` came out true and pt#5827
-- (Over 1.5 bought at 0.230, odds 4.348, entered at 88') was booked as WON on a
-- match that never had a second goal.
--
-- The failure is one-sided by construction: a flap can only push a maximum UP,
-- so every score-feed error lands as a fabricated WIN on an over. It cannot
-- produce a fabricated loss. Any yield computed off these rows is biased high.
--
-- MEASURED BLAST RADIUS (all 108 settled entries re-checked against
-- api-football's full-time score, 96 distinct fixtures):
--   3 mislabelled, all false WINs — pt#5525 (Vitoria v Botafogo, tape 2 /
--   true 1), pt#5780 (Celje v Slovan Bratislava, tape 3 / true 2), pt#5827
--   (Aberdeen v Rangers, tape 2 / true 1). No false losses, as expected.
--   657 of 6,170 fixtures on the tape carry a non-monotone score at some point.
--
-- Strategies 17 and 18 are NOT affected: both already ask api-football for the
-- half-time score first and fall back to the tape only when it will not answer.
-- All 46 s17 and 31 s18 settled entries were re-checked and every one agrees.
--
--
-- WHAT CHANGED IN THE CODE (agent/pressure_agent.py)
--
-- 1. `_final_goals_api()` — batched /fixtures?ids=, 20 per call, returns a
--    total only for FT/AET/PEN. A missing key means "not settleable", never
--    "0 goals".
-- 2. settle() takes the API total as `final_goals` whenever it exists. The tape
--    maximum survives ONLY as a fallback for fixtures the API will not answer,
--    and still only once our own polls ran past 88'.
-- 3. `goals_at_plus_10` / `goal_next_10` are discarded when the tape claims more
--    goals at that minute than the match ever had, rather than recorded as a
--    fabricated positive into the calibration arm.
-- 4. The paper trade now settles on `final_goals > target_line` — the line the
--    token was actually bought on — instead of "the score moved off what we
--    read at entry". Where the tape was wrong at entry the LINE is wrong too,
--    and only the line matches what the token pays.
--
-- This column records which source produced `final_goals`, so a settled row can
-- never again be trusted without knowing where its outcome came from.

ALTER TABLE pressure_observations
    ADD COLUMN IF NOT EXISTS final_goals_source text;

COMMENT ON COLUMN pressure_observations.final_goals_source IS
    'api = api-football full-time score (authoritative). '
    'poll = maximum of our own score tape, used only when the API would not '
    'answer for the fixture; one-sided upward bias, see db/038.';

-- Rows settled before this migration were all tape maxima.
UPDATE pressure_observations
   SET final_goals_source = 'poll'
 WHERE final_goals IS NOT NULL
   AND final_goals_source IS NULL;
