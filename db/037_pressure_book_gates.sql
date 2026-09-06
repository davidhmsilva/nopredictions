-- 037_pressure_book_gates.sql
--
-- Registers the book-quality gates added to all three pressure arms on
-- 2026-08-30/31, and the two measurements that motivated them — one of which
-- says the pressure signal those arms are built on does not exist.
--
-- Nothing here changes a stored row. It records what each obs_version now means
-- and pre-registers the claim the gates make, because the gates were fitted on
-- recorded data and are therefore NOT yet a result.
--
--
-- WHAT WAS MEASURED
--
-- Reviewed at exactly 100 entries on strategy 16. Yield -2.07% gross, CI
-- [-22.4, +18.2] — a +/-20pp interval against a 2-4pp target, so the settlement
-- arm cannot answer anything and was set aside. Everything below is the
-- calibration arm: `real - ask` (realised outcome minus the price we would have
-- paid), one row per fixture/minute/score, CIs clustered by fixture.
--
--   strategy 16, 6,449 rows / 494 fixtures, minute 70-89, obs_version >= 2
--     spread  0-3pp   +3.64      depth after spread<=6pp:  $0-200      -4.72
--     spread  3-6pp   +0.92                                $200-1000   -0.49
--     spread 6-10pp   -4.26                                $1000-5000  +3.63
--     spread 10-20pp  -6.33  CI[-12.1, -0.6]               $5000+      +4.61
--     spread 20pp+   -38.38  CI[-44.3,-32.5]
--
--   strategy 17, 3,420 rows / 552 fixtures, minute 15-25, still 0-0
--     spread  0-3pp   +0.41      depth after spread<=10pp: $0-25       +2.19
--     spread  3-6pp   -0.77                                $25-200     -1.45
--     spread 6-10pp   -6.01  CI[-11.0, -1.0]               $200-1000   -7.12
--     spread 20pp+   -38.93  CI[-47.4,-30.4]               $1000+      +0.40
--
--   strategy 18, 4,654 rows / 635 fixtures, minute 15-25, still 0-0
--     spread  0-3pp   -1.83  CI[-5.62,+1.96]               $0-25       +0.94
--     spread  3-6pp   -6.97  CI[-11.27,-2.67]              $25-200     -6.55
--     spread 6-10pp   -7.88  CI[-13.64,-2.12]              $200-1000   -5.44
--     spread 20pp+   -43.86                                $1000+      -2.69
--
-- The 20pp+ buckets are not markets. On strategy 16 that is 430 rows over 221
-- fixtures quoting an ask around 0.90 that resolves at 0.529 — a lone sell order
-- parked far from any bid. MAX_ASK cannot catch them: the defect is the empty
-- ladder, not the level of the price.
--
-- The gates are therefore per-market and deliberately NOT copied between arms:
-- 6pp on 16 and 17, where the break is at 6pp; 3pp on 18, where 0-3pp is the
-- only bucket whose CI still contains zero. Depth was raised only on strategy 16
-- ($50 -> $1000), where it is monotone; on 17 and 18 it comes out non-monotone
-- and no floor above the existing $25 is supported by anything.
--
-- The case that shows depth is the wrong instrument: CA Mineiro vs EC Vitoria,
-- 2026-08-29, minute 15. The 1st Half O/U 0.5 book quoted bid 0.55 / ask 0.99 on
-- $30,117 of depth, and traded at 0.56 two minutes later. Every depth floor
-- passes that quote. Only the spread rejects it.
--
-- What the gate is worth, stated as the negative claim: on strategy 16 the
-- apparent "PM's late over is expensive" reading (-5.11pp at 81-83', -6.02pp at
-- 84-86', both CIs clear of zero) is ENTIRELY an artefact of these books. On a
-- clean book the ask is fair to slightly cheap at every minute in the window
-- (+2.13 / +2.86 / +4.66 / +2.98 / +3.92pp across 70-74 / 75-78 / 79-82 /
-- 83-86 / 87-89). The gate removes a measured bleed. It does not find an edge —
-- every one of those positive numbers still has a CI crossing zero.
--
--
-- WHY THIS IS PRE-REGISTERED AND NOT A RESULT
--
-- The thresholds were chosen by looking at the same recorded rows that are then
-- used to show they help. The entry splits below are IN-SAMPLE and must never be
-- quoted as performance:
--
--     s16   85 -> 56 entries    -0.86% -> +5.31%   (dropped: -12.41%)
--     s17   46 -> 34 entries    -9.41% -> -0.62%   (dropped: -33.95%)
--     s18   31 -> 20 entries   -14.22% -> +5.63%   (dropped: -45.16%)
--
-- They are the reason to believe the gate is worth forward-testing, not evidence
-- that it works. Hence H-PRESSURE-BOOK below, whose test is forward-only.
--
--
-- THE FINDING THAT UNDERCUTS ALL THREE ARMS
--
-- Recorded here because it is the honest context for any later verdict, and
-- because it was measured on far more data than any entry count will reach.
--
-- Strategy 16, `real - fair_base` by pressure quintile, minute 75-88:
-- +4.82 / +10.82 / +13.84 / +9.61 / +7.29pp — non-monotone, peaking in the
-- MIDDLE. On clean books, press >= 45 gives +2.35pp and press < 45 gives
-- +5.28pp: the gate selects the WORSE rows. The +5.8pp that entries show against
-- the table is not the pressure signal — it is a uniform table bias, present at
-- pressure zero (`real - fair_base` = +5.68 / +5.98 / +5.98 / +6.19pp across
-- 70-74 / 75-79 / 80-84 / 85-89, CIs clear of zero). late_goals_table runs about
-- 6pp low everywhere, so every recorded edge_base_pp is inflated until it is
-- refitted.
--
-- MIN_PRESSURE was left in place on all three arms anyway. Removing it is a
-- decision about what these strategies ARE, not a bug fix, and it is not made
-- here.

COMMENT ON COLUMN pressure_observations.obs_version IS
  'Splits the entry population; never pool two versions in one yield. '
  '1 = edge >= 2pp AND pressure >= 45. '
  '2 (2026-08-15) = the entry rule became a PREDICTION — minute + pressure + '
  'executability, no price comparison; fair_* still recorded as the null. '
  '3 (2026-08-26) = the AXIS moved, not the rule: danger_index renormalises its '
  'remaining weights when the feed carries no xG, so fixtures that could never '
  'clear the bar for lack of an xG feed now can. '
  '4 (2026-08-30) = book-quality gates — ask depth $50 -> $1000, new 6pp spread '
  'cap, two-sided book required. See db/037 and H-PRESSURE-BOOK. Observation '
  'rows are unaffected across all four: best_bid, best_ask and ask_depth_usd are '
  'stored raw, so any version''s gate is reconstructible over the whole series.';

COMMENT ON COLUMN ht_pressure_observations.obs_version IS
  'Splits the entry population; never pool two versions in one yield. '
  '1 = 0-0 at minute 15-25 with opening pressure >= MIN_PRESSURE. '
  '2 (2026-08-31) = adds a 6pp spread cap and a two-sided-book requirement; the '
  '$25 depth floor is UNCHANGED because depth is non-monotone on this market. '
  'Threshold measured on this book, not inherited from strategy 16 — see db/037.';

COMMENT ON COLUMN fav_ht_observations.obs_version IS
  'Splits the entry population; never pool two versions in one yield. '
  '1 = pre-match favourite, still 0-0 at 15-25, pressing and out-pressing. '
  '2 (2026-08-31) = adds a 3pp spread cap and a two-sided-book requirement; the '
  '$25 depth floor is UNCHANGED. The cap is 3pp and NOT the 6pp used on the '
  'sibling arms because 0-3pp is the only bucket on this book whose CI contains '
  'zero. See db/037.';

-- Pre-registration. Stated before a single obs_version 4 / 2 entry has settled,
-- because the gate was chosen by looking at recorded rows and the tempting
-- failure is to quote that same in-sample improvement as the result.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT
  'H-PRESSURE-BOOK — the book-quality gates survive out of sample',
  'Across the three pressure arms, entries taken only on a two-sided book inside '
  'the per-market spread cap realise at or above the price paid, where the '
  'ungated population does not. '
  'PRE-REGISTERED PREDICTION: on entries recorded FORWARD from 2026-08-31 at '
  'pressure_observations.obs_version = 4 and ht_/fav_ht_observations.obs_version '
  '= 2, `real - ask` is >= 0 with a fixture-clustered CI excluding zero, and the '
  'rows the gates reject continue to run materially negative. '
  'FALSIFIED IF: forward `real - ask` on gated entries is indistinguishable from '
  'the ungated population — that would mean the spread buckets were an artefact '
  'of two weeks of one summer and not a property of these books. '
  'PRIMARY TEST: `real - ask` on ALL observation rows carrying a book (entered or '
  'not), split by spread bucket, clustered by fixture — it has orders of '
  'magnitude more power than any entry count these arms will reach. The entry '
  'yield comparison is secondary and, at the observed entry rate, will not be '
  'decisive for months. '
  'NOT EVIDENCE: the in-sample splits (s16 85->56, s17 46->34, s18 31->20) that '
  'motivated the gate. The thresholds were fitted on those same rows.',
  'A spread of 20pp+ is not a wide market, it is an absent one: 430 rows over 221 '
  'fixtures quoted an ask near 0.90 that resolved at 0.529, and $30k of depth sat '
  'behind an ask of 0.99 on a market trading at 0.56 two minutes later. Against '
  'that: deep, tight books belong to the larger competitions, so part of any '
  'improvement is competition mix rather than book quality, and that confound is '
  'not controlled. The claim being made is the negative one — a bleed removed, '
  'not an edge found.',
  'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-BOOK%');

-- A measured negative, recorded so it is not re-asked. Methodological rule 3:
-- every hypothesis tested goes in the table, including the ones that die.
INSERT INTO research_hypotheses (title, description, rationale, source, status, verdict, verdict_at, created_by)
SELECT
  'H-PRESSURE-1H-AGG — how the two danger indices are combined changes nothing',
  'Strategy 17 aggregates the two sides'' danger indices with a MEAN, so a '
  'one-sided opening (32 vs 3) scores the same as an even one (18 vs 17) and '
  'fails the gate. Tested whether MAX or SUM predicts a first-half goal better.',
  'The over pays on a goal from either end, so a side attacking alone arguably '
  'counts in full rather than half. CA Mineiro vs EC Vitoria (2026-08-29) is the '
  'shape: opening 31.65 vs 3.38 cleared the favourite arm on its own index and '
  'gap, and missed the over arm''s mean gate at 17.52 against 19.0.',
  'user', 'rejected',
  'VERDICT 2026-08-31: NOT SUPPORTED. No aggregation of the two danger indices '
  'carries information about the first-half goal once minute and the pre-match '
  'total are held fixed.

HALF THE QUESTION IS VACUOUS: sum = 2 x mean, a monotone linear rescale, so no
logistic model can tell them apart. Only max (and min) are distinct features.

n = 338 fixtures with an opening (15-18'') reading and a settled first half.
Control = minute + pre-match total. Out-of-sample 5-fold CV, paired per-fixture
delta log-loss, averaged over 40 seeds, 4,000-sample bootstrap CI:

  mean   delta = +0.00380  CI[+0.00278,+0.00498]   WORSE than the control
  max    delta = +0.00265  CI[-0.00218,+0.00750]   indistinguishable from zero

Raw rates by tercile are flat, all +/-9pp:
  mean   low 58.4%  mid 58.0%  high 61.9%
  max    low 61.1%  mid 58.0%  high 59.3%

The gate as shipped (mean >= 19) selects 65 of 338 fixtures at 61.5% against
59.0% below it.

Both coefficients point the wrong way (more pressure -> fewer first-half goals),
but the sign flips between the 184-fixture cut that requires fair_base and the
338-fixture cut that does not. Read it as zero, not as an inversion.

DECISION: the mean is NOT replaced by the max. Doing so would have admitted the
Atletico-MG fixture and there is no evidence it makes money. The live box-score
reading is at its ceiling on all three arms, which is the third independent
replication of the same result (see the pressure-quintile figures in db/037 for
strategy 16, and finding_live_reading_ceiling).

WHAT WOULD REOPEN THIS: a pressure input that is not the box score. Nothing in
api-football''s aggregate has beaten minute + score + pre-match total yet.',
  now(), 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-PRESSURE-1H-AGG%');
