-- 042 — strategy 17 stops taking anything shorter than 1.75 (obs_version 4)
--
-- WHAT CHANGED
-- ------------
-- The Live Pressure HT Over 0.5 arm now has TWO gates instead of one, and they
-- are separate events rather than a single test:
--
--   signal   still 0-0, minute 15-40, pressure_now >= MIN_PRESSURE (19)
--   price    the CLOB ask quotes 1.75 or longer (ask <= 0.5714)
--
-- A fixture that clears the signal gate while the book is still shorter than
-- 1.75 is NOT dropped. It is ARMED and monitored, and enters on the first later
-- poll where the price has arrived and the book is still clean (two-sided,
-- spread <= 6pp, >= $25 of ask depth, score pinned, still 0-0, still <= 40').
-- The arming poll's minute and reading are written to every row from that point
-- on, and entry_trigger says whether pressure was passing at the moment of
-- entry ('live') or only when the fixture was armed ('armed').
--
-- ⚠️ NEVER POOL v4 WITH v1-v3. The entry universe is different.
--
-- WHAT THE PRICE GATE ACTUALLY SELECTS
-- ------------------------------------
-- Mostly the clock. On 5,436 clean 0-0 polls between 15' and 40', the share of
-- the book already quoting 1.75 or longer:
--
--   15-19'   41%   median ask 0.590 (1.69)      30-34'   99%   0.370 (2.70)
--   20-24'   77%              0.530 (1.89)      35-40'  100%   0.280 (3.57)
--   25-29'   95%              0.460 (2.17)
--
-- The ask falls with the clock because the FAIR VALUE falls with the clock — the
-- empirical first-half table runs ~0.59 at 15' to ~0.19 at 40'. So "wait for
-- 1.75" is close to "wait until ~25'", and db/040 already measured that later is
-- not cheaper: `real - ask` on clean books is about equally negative at every
-- minute from 15' to 40' (-3.39 / -4.90 / -3.14 / -2.32 / -5.43pp, every CI
-- crossing zero). The longer price is leverage on whatever the pressure signal
-- is worth, not an edge of its own.
--
-- THE FUNNEL, REPLAYED OVER STORED ROWS
-- -------------------------------------
-- Of 121 fixtures that ever armed (pressure >= 19, still 0-0, 15-40'), applying
-- the shipped book gates:
--
--   11   enterable at the arming poll itself
--   49   reached 1.75 on a LATER poll while still 0-0 (median wait 9 minutes,
--        p90 13, max 19) — these are the entries the monitoring state exists for
--   61   never reached 1.75 on a clean book before a goal or half time
--
-- So the funnel roughly halves and the surviving entries move later and longer.
-- This is a count on stored rows, not a result: nothing here says the 60 that
-- survive win, or that the 61 dropped would have lost.
--
-- WHAT THE ODDS BANDS SAID BEFORE THIS CHANGE
-- -------------------------------------------
-- 68 settled entries (v1 n=46, v2 n=21, v3 n=1), 1u flat, net of the taker fee:
--
--   band          n   won    hit   implied   net yield   95% CI
--   1.00-1.50    16    11  0.688     0.716       -4.6%   [-39.4, +23.8]
--   1.50-1.75    31    18  0.581     0.620       -9.0%   [-35.8, +17.7]
--   1.75-2.00    16     9  0.562     0.551       +0.4%   [-45.3, +45.4]
--   2.00-2.50     5     3  0.600     0.474      +23.3%   [-61.8, +108.4]
--   ALL          68    41  0.603     0.616       -3.4%   [-22.9, +15.9]
--
-- The hit rate tracks the implied price band by band, there is no gradient, and
-- every interval is 6-25x too coarse for the effect being hunted. This change is
-- therefore a DECISION about what the strategy is, taken by the user, and not a
-- finding acted on. It is recorded as a new obs_version so the question stays
-- answerable rather than averaged away.

ALTER TABLE ht_pressure_observations
    ADD COLUMN IF NOT EXISTS armed_at_minute SMALLINT,
    ADD COLUMN IF NOT EXISTS armed_pressure  REAL,
    ADD COLUMN IF NOT EXISTS entry_trigger   TEXT;

COMMENT ON COLUMN ht_pressure_observations.armed_at_minute IS
  'obs_version 4: the minute this fixture FIRST cleared the pressure gate, '
  'after which it is monitored until the ask reaches MIN_ODDS (1.75). NULL '
  'means the fixture never armed. Only the first arming is kept.';
COMMENT ON COLUMN ht_pressure_observations.armed_pressure IS
  'The pressure_now reading at the arming poll — the reading that made this '
  'fixture a candidate, and the one an entry made minutes later has to be '
  'judged against.';
COMMENT ON COLUMN ht_pressure_observations.entry_trigger IS
  '''live'' — pressure was passing at this poll; ''armed'' — it passed earlier '
  'and this is the poll at which the PRICE arrived, with the reading possibly '
  'cooled since. NULL when the fixture never armed. The two are different bets '
  'and must never be pooled.';

-- Armed-and-waiting is a state worth querying directly: it is the whole cost of
-- the price gate, and a shrinking entry count on its own does not distinguish
-- "nothing pressed" from "everything pressed too early to be priced".
CREATE INDEX IF NOT EXISTS ht_obs_armed_idx
    ON ht_pressure_observations (fixture_id, minute)
    WHERE armed_at_minute IS NOT NULL;

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE-MINODDS — refusing anything under 1.75 pays for the entries it loses',
  'From obs_version 4 strategy 17 enters only when the CLOB ask quotes 1.75 or '
  'longer. A fixture that clears the pressure gate at a shorter price is armed '
  'and monitored, and enters when the price arrives. '
  'PRE-REGISTERED PREDICTION: v4 entries settle at a yield above the v1-v3 '
  'series after the taker fee, and the fixtures dropped for never reaching 1.75 '
  'settle below it. '
  'FALSIFIED IF: the dropped fixtures are no worse than the kept ones, which '
  'would mean the gate is selecting on the clock and costing half the sample for '
  'nothing. '
  'PRIMARY TEST: on ALL observation rows carrying a clean book (far more power '
  'than the entries), P(goal before HT) - ask by ask bucket, with minute and '
  'pre-match total as controls, one row per fixture-minute clustered by fixture. '
  'SECONDARY: entry_trigger ''live'' vs ''armed'' on the same arm — an entry made '
  'on a reading that has since cooled is not the same bet. '
  'NULL: db/040 measured `real - ask` as about equally negative at every minute '
  'from 15'' to 40'' and the 68-entry odds-band split showed hit rate tracking '
  'the implied price with no gradient. If that carries over, this gate changes '
  'the price and the entry count and nothing else.',
  'The user''s decision, taken on the odds-band split of the first 68 settled '
  'entries: the two bands at 1.75 and longer were the only ones not losing '
  '(+0.4% and +23.3% net, n=16 and n=5), against -4.6% and -9.0% below 1.75. '
  'Against it: every one of those intervals is at least 26pp wide against a '
  '2-4pp target, so the split is a description and not a result — and the ask '
  'crosses 1.75 as a function of the clock (41% of clean books by 15-19'', 95% '
  'by 25-29''), which is a mechanism that has already been measured to carry no '
  'price advantage. Recorded as a decision about what this strategy is.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-MINODDS%');
