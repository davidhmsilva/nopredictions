-- 040 — the pressure reading stops being frozen at 15-18' (s17 + s18)
--
-- WHAT CHANGED
-- ------------
-- Both first-half arms measured pressure ONCE, at the first poll landing in
-- 15-18', and then froze it. A fixture whose favourite spent the opening quarter
-- of an hour walking the ball around and only started pressing at 28' could
-- never be entered, however hard it pressed afterwards. Manchester City vs
-- Coventry (fixture 1557394, 2026-09-05) is the case that prompted this: 85%
-- possession and ZERO shots at 15' gave a dominance of +16.7 against a gate of
-- 20, and by 22' the same index read +32 — with the reading frozen and the entry
-- window closed at 25'.
--
-- From obs_version 3 the arms re-measure at EVERY poll and may enter at any
-- minute up to 40':
--
--   minute <= 18   the cumulative totals scaled to a 15-minute rate. This is
--                  literally the old opening reading — entries at 15-18' are
--                  numerically unchanged, so the two versions are continuous
--                  there.
--   minute >  18   the ROLLING 15-minute window (live_tracker's deltas), which
--                  is what "pressing NOW" means and is the same measure the
--                  sibling full-match arm has always used.
--   no window      no reading, no entry. A fixture picked up at 30' with no
--                  baseline is still recorded and skipped rather than back-
--                  filled from a longer average: a fallback that looks like the
--                  real measurement is how a strategy ends up evaluated on a
--                  quantity it never traded.
--
-- The frozen opening columns are still written on every row exactly as before.
-- They are the control: "does the opening-15 reading beat the rolling one" stays
-- a question the data can answer, and the v1/v2 series remains comparable.
--
-- WHY THE THRESHOLDS DID NOT MOVE
-- -------------------------------
-- The rolling index was reconstructed offline from the cumulative stats already
-- stored on these tables (4,557 poll-rows, minutes 19-44, still 0-0, deltas
-- against the row nearest minute-15):
--
--   both ends   p25  8.3   p50 13.3   p75 18.8   p90 24.4   p95 27.7
--   per side    p25  6.2   p50 11.7   p75 19.6   p90 30.4   p95 36.7
--   |gap|       p25  4.6   p50 10.0   p75 18.8   p90 28.8   p95 33.8
--
-- Those are within a point of the opening-15 distribution the current gates were
-- calibrated on (median 15, p73 19, p89 25), and they are flat across the clock
-- (p75 of 17.7 at 25-29' against 19.4 at 40-44'). So MIN_PRESSURE = 19,
-- MIN_FAV_PRESSURE = 19 and MIN_DOMINANCE = 20 keep meaning what they meant —
-- roughly the top quartile — on the new axis. Nothing was refitted to an
-- outcome.
--
-- WHAT THIS DOES NOT BUY
-- ----------------------
-- Entering later gets longer odds; it does not get a cheaper market. `real - ask`
-- on clean books (<=6pp spread), still 0-0, clustered by fixture:
--
--   s17  15-19' -3.39pp  20-24' -4.90  25-29' -3.14  30-34' -2.32  35-40' -5.43
--   s18  15-19' -3.48pp  20-24' -3.62  25-29' -1.81  30-34' -2.31  35-40' -2.88
--
-- HOW MUCH WIDER THE FUNNEL GETS
-- ------------------------------
-- Replaying both gates over the stored rows (the rolling index reconstructed as
-- above; book gates applied as shipped), counting distinct fixtures that would
-- have had at least one enterable poll:
--
--   s17   60 -> 102 fixtures   (+64 new, 22 that the old gate caught and this
--   s18   63 ->  90 fixtures   (+40 new, 13 lost)  one does not)
--
-- The losses are real and intended: a fixture whose opening was hot but which
-- had gone quiet by the time the book was clean no longer qualifies, because the
-- gate now asks what is happening NOW. This is a funnel count on stored rows,
-- not a result — nothing here says the extra entries win.
--
-- Every CI crosses zero and none of them narrows with the clock: the ask is
-- about equally rich at every minute. The longer price is leverage on whatever
-- edge the pressure filter has, not an edge of its own — and the 100-game review
-- (db/037) is on record that the pressure gate selected nothing at all on the
-- full-match arm. This is a widening of the funnel, pre-registered as such.

ALTER TABLE ht_pressure_observations
    ADD COLUMN IF NOT EXISTS pressure_now    REAL,
    ADD COLUMN IF NOT EXISTS pressure_source TEXT,
    ADD COLUMN IF NOT EXISTS pressure_minute SMALLINT,
    ADD COLUMN IF NOT EXISTS has_window      BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN ht_pressure_observations.pressure_now IS
  'The reading the entry gate is applied to from obs_version 3: cumulative '
  'scaled to 15 minutes at <=18'', the rolling 15-minute window after that. '
  'NULL when no window baseline exists. opening_pressure remains the frozen '
  '15-18'' control.';
COMMENT ON COLUMN ht_pressure_observations.pressure_source IS
  '''opening'' (cumulative, <=18'') or ''window'' (rolling 15-minute deltas).';
COMMENT ON COLUMN ht_pressure_observations.pressure_minute IS
  'Minute the stats behind pressure_now were true at — not necessarily this '
  'poll''s minute, since a snapshot is carried forward between paid calls.';

ALTER TABLE fav_ht_observations
    ADD COLUMN IF NOT EXISTS fav_pressure_now REAL,
    ADD COLUMN IF NOT EXISTS dog_pressure_now REAL,
    ADD COLUMN IF NOT EXISTS dominance_now    REAL,
    ADD COLUMN IF NOT EXISTS pressure_source  TEXT,
    ADD COLUMN IF NOT EXISTS pressure_minute  SMALLINT,
    ADD COLUMN IF NOT EXISTS has_window       BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN fav_ht_observations.dominance_now IS
  'fav_pressure_now - dog_pressure_now, the gate from obs_version 3. '
  'opening_dominance remains the frozen 15-18'' control, and the two are equal '
  'by construction on a row entered at 15-18''.';

-- The dashboard must show the number the entry was actually made on, not the
-- opening reading that no longer decides anything.
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
    coalesce(o.pressure_now, o.opening_pressure) AS pressure_index,
    o.goal_minute,
    o.goal_minute_source,
    o.goal_before_ht    AS won,
    o.ht_goals          AS final_goals,
    o.observed_at       AS entered_at
FROM ht_pressure_observations o
WHERE o.entered AND o.paper_trade_id IS NOT NULL;

GRANT SELECT ON v_ht_pressure_trades TO anon, authenticated;

CREATE OR REPLACE VIEW v_fav_ht_trades AS
SELECT
    o.paper_trade_id,
    o.fixture_id,
    o.obs_version,
    o.home,
    o.away,
    o.league,
    o.minute                AS entry_minute,
    o.goals_total           AS goals_at_entry,
    NULL::real              AS target_line,
    o.best_ask              AS entry_price,
    coalesce(o.fav_pressure_now, o.opening_fav_pressure) AS pressure_index,
    NULL::int               AS goal_minute,
    o.ht_source             AS goal_minute_source,
    o.fav_led_at_ht         AS won,
    (o.ht_home_goals + o.ht_away_goals) AS final_goals,
    o.observed_at           AS entered_at
FROM fav_ht_observations o
WHERE o.entered AND o.paper_trade_id IS NOT NULL;

GRANT SELECT ON v_fav_ht_trades TO anon, authenticated;

-- Pre-registered, because this is a change to WHAT is being tested and not a bug
-- fix. Both arms keep their original hypothesis; this one is about the widening.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE-LATE — a rolling pressure reading enters later, at longer odds, no worse',
  'From obs_version 3 the first-half arms (strategies 17 and 18) re-measure '
  'pressure at every poll — cumulative to 18'', rolling 15-minute window after — '
  'and may enter up to minute 40 instead of 25. '
  'PRE-REGISTERED PREDICTION: entries triggered by a rolling reading after 18'' '
  'settle at a yield no worse than entries triggered inside 15-18'', measured on '
  'the same arm, after the taker fee. '
  'FALSIFIED IF: the post-18'' entries are materially worse, which would mean the '
  'opening quarter of an hour carried something a later window does not. '
  'PRIMARY TEST: on ALL observation rows carrying a book (far more power than '
  'the entries), P(outcome) - ask regressed on pressure_now with minute, '
  'pre-match expectation and venue as controls, split by pressure_source, one '
  'row per fixture-minute clustered by fixture. '
  'NULL: the 100-game review found the pressure gate selected nothing on the '
  'full-match arm; if that carries over, this widening changes the odds and the '
  'entry count and nothing else.',
  'The frozen reading threw away every fixture whose pressure arrived after 18'' '
  '— Manchester City vs Coventry (2026-09-05) read +16.7 dominance at 15'' and '
  '+32 at 22'', and was unenterable. Against it: `real - ask` is about equally '
  'negative at every minute from 15'' to 40'' (s17 -3.4 to -5.4pp, s18 -1.8 to '
  '-3.6pp, all CIs crossing zero), so a later entry buys leverage on the signal, '
  'not a cheaper price — and the signal itself is unproven.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-LATE%');
