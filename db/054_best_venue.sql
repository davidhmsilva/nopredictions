-- 054 — the in-play arms buy at the cheaper exchange
--
-- WHAT CHANGED
-- ------------
-- Strategies 16 and 17 priced their entry on Polymarket alone because that is
-- the only venue the agents could see. Kalshi lists the same two markets —
-- the match-goals ladder (KX*TOTAL) and the first-half one (KX*1HTOTAL) — on
-- the competitions these arms actually trade, and it is frequently the
-- cheaper of the two.
--
-- From these versions each arm reads BOTH books for the line it wants and
-- books the paper trade at whichever is cheaper NET OF THAT VENUE'S TAKER
-- FEE. Every row records where it went and what the alternative was, so the
-- question "was the second venue worth anything" is answerable from the data
-- rather than from an argument.
--
--   pressure_observations     obs_version 5 -> 6
--   ht_pressure_observations  obs_version 6 -> 7
--
-- ⚠️ NEVER POOL THESE WITH EARLIER VERSIONS. The entry PRICE is drawn from a
--    different population: some fills are now Kalshi's, and the book gates
--    (max ask, max spread, min depth) are applied to whichever venue was
--    chosen rather than always to Polymarket's.
--
-- WHY THIS IS NOT A NEW SIGNAL
-- ---------------------------
-- It changes the price, not the rule. The pressure gate, the minute window,
-- the score requirement and the fair-value table are untouched — a fixture
-- that would have entered still enters, and one that would not still does
-- not. What moves is the cost of the same bet, which is the one lever on a
-- paper record that needs no hypothesis to justify.
--
-- Measured on a live board, 2026-09-20 (the site's football board, which runs
-- the same comparison): 60 of 113 fixtures listed on both exchanges, 89
-- outcomes where one venue was strictly cheaper — 52 Kalshi, 37 Polymarket —
-- median saving 1.3pp net of fees, largest 5.4pp.
--
-- WHAT THE FEE DOES AND DOES NOT DO
-- ---------------------------------
-- Kalshi's taker fee is 0.07·p·(1−p) against Polymarket's 0.05·p·(1−p), 40%
-- more. Netting it does NOT flip which venue is cheaper when one is cheaper
-- by a cent or more: the fee difference is 0.02·p·(1−p), which maxes at
-- 0.005, exactly the half-cent gap below which two quotes are treated as one
-- price. (Searched exhaustively in agent/tests/test_venues.py, which keeps
-- the search as the test that refuted the opposite claim.)
--
-- It halves what a win is worth, and it decides a tie near even money, where
-- the same printed price costs 0.5pp more on Kalshi.
--
-- WHAT CANNOT WIN A PRICE
-- -----------------------
-- A venue whose book for THAT LEG is one-sided or more than 10pp wide. The
-- 100-game review measured that class at −38pp (430 rows quoting an ask near
-- 0.90 that resolved at 0.529), and the tell is the spread, not the depth —
-- CA Mineiro v EC Vitória quoted bid 0.55 / ask 0.99 behind $30,117.
--
-- ⚠️ The grade is read off the LEG being bought, never off the fixture's
--    match-result ladder. A 2¢ 1X2 vouching for a 34¢ Over 2.5 produced a
--    27.3pp phantom "saving" on the site's board before this was fixed.
--
-- STRATEGY 18 IS UNCHANGED
-- ------------------------
-- Kalshi lists no "favourite leading at half time" market, so there is
-- nothing to compare its entry against. Its obs_version does not move and its
-- rows carry venue = 'polymarket' by default, which is the truth.

BEGIN;

ALTER TABLE pressure_observations
  ADD COLUMN IF NOT EXISTS venue            text NOT NULL DEFAULT 'polymarket',
  ADD COLUMN IF NOT EXISTS alt_venue_ask    numeric,
  ADD COLUMN IF NOT EXISTS venue_saving_pp  numeric;

ALTER TABLE ht_pressure_observations
  ADD COLUMN IF NOT EXISTS venue            text NOT NULL DEFAULT 'polymarket',
  ADD COLUMN IF NOT EXISTS alt_venue_ask    numeric,
  ADD COLUMN IF NOT EXISTS venue_saving_pp  numeric;

ALTER TABLE fav_ht_observations
  ADD COLUMN IF NOT EXISTS venue            text NOT NULL DEFAULT 'polymarket',
  ADD COLUMN IF NOT EXISTS alt_venue_ask    numeric,
  ADD COLUMN IF NOT EXISTS venue_saving_pp  numeric;

COMMENT ON COLUMN pressure_observations.venue IS
  'Which exchange the entry was priced on. Every row before obs_version 6 is '
  'polymarket by construction — it was the only venue the agent could see.';
COMMENT ON COLUMN pressure_observations.alt_venue_ask IS
  'The OTHER exchange''s ask for the same bet, when it had a real book for '
  'that leg. NULL means it did not quote it or its book was not a book — '
  'never that it was more expensive.';
COMMENT ON COLUMN pressure_observations.venue_saving_pp IS
  'How much more the alternative would have cost, in probability points, NET '
  'of each venue''s taker fee. NULL when only one venue quoted the leg or the '
  'two were inside half a cent of each other.';

COMMENT ON COLUMN ht_pressure_observations.venue IS
  'Which exchange the entry was priced on (obs_version >= 7; polymarket before).';
COMMENT ON COLUMN ht_pressure_observations.alt_venue_ask IS
  'The other exchange''s ask for the same first-half line, where it had a real book.';
COMMENT ON COLUMN ht_pressure_observations.venue_saving_pp IS
  'Net-of-fee saving against the alternative, in probability points.';

COMMENT ON COLUMN fav_ht_observations.venue IS
  'Always polymarket: Kalshi lists no "favourite leading at half time" market, '
  'so there is nothing to compare this arm against. The column exists so the '
  'three arms read the same.';

-- The forward question, pre-registered.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-BEST-VENUE — buying at the cheaper of two exchanges pays for itself',
  'From obs_version 6 (s16) and 7 (s17) the in-play arms book each paper trade '
  'at whichever of Polymarket and Kalshi is cheaper NET of that venue''s taker '
  'fee, gated on the book of the LEG being bought rather than on the fixture''s '
  'match-result ladder. '
  'PRE-REGISTERED PREDICTION: mean realised saving per CONTESTED entry (both '
  'venues quoting a real book) is above zero with a CI clear of zero, and each '
  'arm''s yield improves by that amount against the same entries repriced on '
  'Polymarket alone. '
  'FALSIFIED IF: venue_saving_pp is ~0 on contested entries, i.e. the two '
  'exchanges quote the same price on the markets these arms trade and the '
  'change is bookkeeping. '
  'PRIMARY TEST: venue / alt_venue_ask / venue_saving_pp are on every row, so '
  'the Polymarket-only counterfactual is RECOVERABLE PER ENTRY rather than '
  'estimated — the two arms of the test are the same bets at two prices, which '
  'is as paired as a comparison gets. '
  'NOT A SIGNAL QUESTION: this changes the price, not the rule. The pressure '
  'gate, the minute window, the score requirement and the fair-value table are '
  'untouched, so it needs no new fair value and cannot be p-hacked by a filter.',
  'The user''s direction, 2026-09-20: carry both venues everywhere and show the '
  'best odds, because "apostar a melhor odd possivel" is both a product claim '
  'and a real improvement to a paper record. Measured on the site''s football '
  'board the same day, which runs this comparison: 60 of 113 fixtures listed on '
  'both exchanges, 89 outcomes where one venue was strictly cheaper (52 Kalshi, '
  '37 Polymarket), median saving 1.3pp net of fees and largest 5.4pp. '
  'Against it: that board is the 1X2 and Over 2.5 across every competition, '
  'while these arms trade one in-play line each on a narrower set — the '
  'contested rate on the entries themselves could be far lower, which is '
  'exactly what the recorded columns are for.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-BEST-VENUE%');

COMMIT;
