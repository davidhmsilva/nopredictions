-- 036_pressure_stats_frozen.sql
--
-- Marks — and retracts — the rows whose pressure reading was manufactured by a
-- frozen stat block between 2026-08-20 and 08-25.
--
-- What happened. `_carry_stats_forward` (commit 06b3ba1, 08-20) copies the last
-- known stats onto every poll that did not re-fetch them. The enrichment gate
-- in live_tracker.poll() decided a fixture needed no paid /fixtures/statistics
-- call by reading `latest.home_shots_total` — which from that commit onward held
-- the CARRIED value. So every fixture got exactly one stats fetch and then never
-- another. Fifteen minutes past that fetch, _find_window_start was differencing
-- a carry against its own source: every window delta went to 0 and danger_index
-- collapsed to its possession term, 5.0 out of 100, for the rest of the match.
-- has_stats and has_window both went on reporting True, so the rows read as
-- measurements of a dead game rather than as the absence of a measurement.
--
-- The signature is unmistakable. Distinct cumulative stat tuples per fixture:
--
--     2026-08-20 -> 08-25   252 of 270 fixtures show exactly ONE
--     2026-08-16 -> 08-20   fixtures show 10-14, as continuous polling implies
--
-- The detector below is the SQL analogue of the code fix (comparing
-- stats_fetched_at in get_signals): a row is frozen when the stat tuple backing
-- it is the same one that backed the baseline ~15 minutes earlier. Validated
-- against the healthy period as a control — it flags 79.0% of measured rows in
-- the broken window and 0.5% before it.
--
-- Why the derived values are RETRACTED and not merely flagged. Every query that
-- judges this strategy — report(), and the goal_next_10 regression the pressure
-- term is measured against — selects on `pressure_index IS NOT NULL`. A flag
-- those queries do not read would leave 13,323 settled fabrications inside the
-- fit. Nulling them makes history identical to what the fixed agent now writes.
--
-- Nothing is lost. pressure_index / home_danger / away_danger are pure functions
-- of columns that stay on the row, verified exact on 3,000 of 3,000 sampled rows
-- (danger_index over the *_window counters and possession; strategy 16 does not
-- renormalise for missing xG — see LiveMatchTracker._danger_index). Combined
-- with stats_frozen the retracted set is reproducible in full.
--
-- No entered row is affected: 0 of the 17,390 flagged rows carry entered = true,
-- so no paper trade loses the reading it was taken on.
--
-- Belt and braces, following the convention of the 2026-08-15 dupe cleanup: the
-- retracted values were dumped first to
-- reports/pressure_stats_frozen_retracted_2026-08-25.csv (id + the six columns
-- nulled + the previous has_window and skip_reason). reports/ is untracked, so
-- that file is local to the machine this ran on.

ALTER TABLE pressure_observations
  ADD COLUMN IF NOT EXISTS stats_frozen boolean;

COMMENT ON COLUMN pressure_observations.stats_frozen IS
  'True when this row''s window baseline carried the SAME stats fetch as the row '
  'itself, so every window delta was structurally zero and the pressure reading '
  'was an artefact, not a measurement — the 2026-08-20..08-25 defect. Its derived '
  'columns (pressure_index, home_danger, away_danger, pressure_factor, '
  'fair_pressure, edge_pressure_pp) are retracted to NULL on these rows and are '
  'recomputable from the *_window counters and possession if ever needed. False '
  'means the window was measured against a genuinely earlier fetch. NULL means '
  'there was no pressure reading to judge in the first place.';

BEGIN;

CREATE TEMP TABLE _frozen ON COMMIT DROP AS
SELECT r.id
FROM pressure_observations r
JOIN LATERAL (
  SELECT b.home_shots_total, b.away_shots_total, b.home_shots_on, b.away_shots_on,
         b.home_corners, b.away_corners, b.home_xg, b.away_xg
  FROM pressure_observations b
  WHERE b.fixture_id = r.fixture_id
    AND b.observed_at < r.observed_at
    AND b.minute BETWEEN r.minute - 20 AND r.minute - 10
  ORDER BY abs(b.minute - (r.minute - 15)), b.observed_at DESC
  LIMIT 1
) base ON true
WHERE r.pressure_index IS NOT NULL
  AND r.has_window
  AND (r.home_shots_total, r.away_shots_total, r.home_shots_on, r.away_shots_on,
       r.home_corners, r.away_corners, r.home_xg, r.away_xg)
      IS NOT DISTINCT FROM
      (base.home_shots_total, base.away_shots_total, base.home_shots_on, base.away_shots_on,
       base.home_corners, base.away_corners, base.home_xg, base.away_xg);

-- Every row that carried a reading is judged; rows that never had one stay NULL.
UPDATE pressure_observations
   SET stats_frozen = false
 WHERE pressure_index IS NOT NULL AND stats_frozen IS NULL;

UPDATE pressure_observations
   SET stats_frozen = true
 WHERE id IN (SELECT id FROM _frozen);

-- Retract the fabrication. skip_reason is only rewritten where it was itself
-- derived from the fabricated index ('pressure N < 45.0'); the book and minute
-- gates on these rows are independent facts and are left exactly as recorded.
UPDATE pressure_observations
   SET pressure_index   = NULL,
       home_danger      = NULL,
       away_danger      = NULL,
       pressure_factor  = NULL,
       fair_pressure    = NULL,
       edge_pressure_pp = NULL,
       has_window       = false,
       skip_reason      = CASE WHEN skip_reason LIKE 'pressure %'
                               THEN 'stats frozen: window baseline is the same fetch'
                               ELSE skip_reason END
 WHERE stats_frozen;

COMMIT;
