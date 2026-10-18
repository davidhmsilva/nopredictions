-- 075_favourite_pressing_ht.sql
--
-- Favourite Pressing HT replaces Live Pressure Favourite HT (strategy 18),
-- 2026-10-12, by the operator's decision, with the rules he confirmed.
--
-- Why (measured 2026-10-11 on 359 clean-book fixtures, outcome from PM's own
-- resolution, one bet per fixture):
--   * strategy 18's gate (favourite >= 19 AND dominance >= 20) selected nothing:
--     117 clean-book entries, -8.4%; its live record is 154 bets, -11.4% net.
--     Against the price, BOTH sides' pressure raised the chance the favourite led
--     at the break, so the difference (dominance) cancelled the signal.
--   * the favourite's own pressure relative to its minute separated: top third
--     +6.8% vs bottom third -24.0%, +30.8pp CI[+4,+60], same sign and size in both
--     halves of the period, both obs_version groups, home and away.
--   * the LEVEL is not established: +6.8% CI[-13,+28]; +1.4% on the v3-v4 axis.
--
-- Strategy 18 is archived, never deleted: its trades reference it. Its
-- observation table carries on, because the new strategy reads the same rows
-- (fav_ht_observations, obs_version 5 from now on).

BEGIN;

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT 'H-FAV-PRESS-REL — a pre-match favourite pressing in the top third for its minute, still 0-0, is not overpriced to lead at half time',
       'Buy PM "<favourite> leading at halftime?" Yes at the ask, 1u, paper, once per fixture, at the first poll where ALL hold: '
       'favourite from PM''s pre-kickoff de-vigged 1X2 with p >= 0.50; 0-0; minute 15-40; the favourite''s own pressure '
       '(fav_pressure_now, rolling 15-minute window) >= the frozen bar for its minute: 15-19'' 25.6, 20-24'' 29.3, 25-29'' 26.2, '
       '30-34'' 27.3, 35-40'' 26.9 (2/3 quantile on clean-book obs_version 3-4 rows, frozen 2026-10-12, never refitted); '
       'no dominance term; book: spread <= 3pp, two-sided, ask depth >= $25, ask <= 0.85. No odds filter. '
       'Settled on PM''s resolution of the token bought. '
       'VERDICT, forward rows only (obs_version >= 5): (1) primary, separation: on observation rows, first row per fixture in the '
       'top third vs first row per fixture in the bottom third of fav_pressure_now for the minute, net yield difference with CI '
       'clear of zero, ~200 fixtures a side; (2) money: the strategy''s net yield CI clear of zero after the taker fee.',
       'Exploratory result of 2026-10-11 (finding_s17_s18_review), turned into a forward test. Strategy 18''s dominance gate '
       'selected nothing; the favourite''s own pressure did. 91 groups were scanned in that analysis, which is why the bar is '
       'frozen and only rows recorded from now on count. The base rate it has to beat: the bottom third paid -24%, '
       'and the market as a whole sits a few points above the realised rate.',
       'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-FAV-PRESS-REL —%');

-- The three sub-rules the analysis surfaced inside the top third. Registered
-- rather than traded (the operator's decision): each was chosen after seeing the
-- data, and on shuffled outcomes the best of the 13 cuts reached +27% one time in
-- seven. Each is read forward only.
INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT t.title, t.description, t.rationale, 'user', 'pre_registered', 'claude'
  FROM (VALUES
   ('H-FAV-PRESS-REL-A — inside the top third, a favourite priced 50-60% pre-match',
    'Subset of Favourite Pressing HT''s own trades: fav_prob in [0.50, 0.60). Read straight off the strategy''s record, no separate entry. Verdict: net yield CI clear of zero AND above the strategy''s other trades.',
    'Exploratory 2026-10-11: n=111, +19.8% CI[-10,+50]; +37% on obs_version 1-2, +9% on 3-4 (n=68). Selected after seeing the data.'),
   ('H-FAV-PRESS-REL-B — inside the top third, odds of 4.00 or longer',
    'Observation rows only, no trades: first row per fixture meeting every Favourite Pressing HT condition AND best_ask <= 0.25. It enters later than the strategy does, so it is not a subset of its trades. Verdict: net yield CI clear of zero.',
    'Exploratory 2026-10-11: n=82, +18.4% CI[-24,+61]; +34% on v1-v2, +6% on v3-v4 (n=45). 64 of its 82 fixtures are also in sub-rule C.'),
   ('H-FAV-PRESS-REL-C — inside the top third, minute 30-34',
    'Observation rows only, no trades: first row per fixture meeting every Favourite Pressing HT condition with minute between 30 and 34. Verdict: net yield CI clear of zero.',
    'Exploratory 2026-10-11: n=86, +23.4% CI[-15,+63]; +54% on v1-v2, -1% on v3-v4 (n=48). Largely the same fixtures as sub-rule B.')
  ) AS t(title, description, rationale)
 WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses h WHERE h.title = t.title);

INSERT INTO strategies (hypothesis_id, name, source, owner_id, theory, interpretation, rules, run_status, promoted_at)
SELECT h.id, 'Favourite Pressing HT', 'agent',
       (SELECT owner_id FROM strategies WHERE id = 18),
       'A pre-match favourite that is pressing hard for the minute it is in, while still level, is not overpriced to lead at half time, the way one pressing little is.',
       'Back the pre-match favourite (50%+) to lead at half time when it is 0-0 between 15'' and 40'' and its own pressure is in the top third for that minute. 1u at the Polymarket ask, paper.',
       '{"phase": "in-play", "venue": "polymarket", "market": "<favourite> leading at halftime? (Yes)", '
       '"execution": "taker at the ask", "stake_u": 1, "self_settling": true, "obs_version": 5, '
       '"min_fav_prob": 0.50, "minutes": [15, 40], "score": "0-0", '
       '"fav_pressure_bar": {"15-19": 25.6, "20-24": 29.3, "25-29": 26.2, "30-34": 27.3, "35-40": 26.9}, '
       '"dominance": "not used", "odds_filter": "none", '
       '"book": {"max_spread": 0.03, "two_sided": true, "min_ask_depth_usd": 25, "max_ask": 0.85}, '
       '"replaces": 18}'::jsonb,
       'running', now()
  FROM research_hypotheses h
 WHERE h.title LIKE 'H-FAV-PRESS-REL —%'
   AND NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Favourite Pressing HT');

UPDATE strategies
   SET retired_at = now(), run_status = 'paused', is_public = false,
       retirement_reason = 'archived 2026-10-12 by its owner: replaced by Favourite Pressing HT. '
                           'The dominance gate selected nothing (154 bets, -11.4% net); '
                           'see db/075 and H-FAV-PRESS-REL.'
 WHERE id = 18 AND retired_at IS NULL;

COMMIT;
