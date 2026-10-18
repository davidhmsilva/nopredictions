-- 076_ht_under_quiet.sql
--
-- Low Pressure HT Under 0.5: buy the "1st Half O/U 0.5" UNDER at the ask when a
-- fixture is 0-0 between 15' and 40' and its pressure is in the bottom third for
-- the minute. By the operator's decision, 2026-10-12. Agent: ht_under_agent.py,
-- riding on s17's rows inside pressure_agent.py.
--
-- Measured 2026-10-11/12 on 1,379 clean-book fixtures (outcome from PM's own
-- resolution): the Over's ask sits 3.3pp above what happens, the Under's is fair
-- (+0.03pp), so the Under at the ask loses about the fee (-2.5%). Quiet games:
-- +0.9% (n=479, CI[-8,+10]). With the frozen bar on the current axis (v6-v7):
-- +4.1% on 279 fixtures, in-sample. Nothing here is established.
--
-- Apply BEFORE deploying the code: ht.write() names the new columns.

BEGIN;
SET LOCAL lock_timeout = '10s';

-- Metadata-only on Postgres 11+ (constant defaults), so the lock is brief.
ALTER TABLE ht_pressure_observations
    ADD COLUMN IF NOT EXISTS under_token_id       text,
    ADD COLUMN IF NOT EXISTS under_ask            real,
    ADD COLUMN IF NOT EXISTS under_would_enter    boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS under_entered        boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS under_paper_trade_id integer,
    ADD COLUMN IF NOT EXISTS under_skip_reason    text;

COMMENT ON COLUMN ht_pressure_observations.under_ask IS
  'The Under''s own CLOB ask, read when every other gate passed (db/076). NULL otherwise.';

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT 'H-HT-UNDER-QUIET — in a quiet first half (bottom third of pressure for the minute, still 0-0), PM''s 1st Half Under 0.5 is not overpriced',
       'Buy PM "1st Half O/U 0.5" Under at its own CLOB ask, 1u, paper, once per fixture, at the first poll where ALL hold: '
       '0-0; minute 15-40; s17''s pressure_now (both ends; cumulative to 18'', rolling 15-minute window after) BELOW the frozen '
       'ceiling for its minute: 15-19'' 11.4, 20-24'' 13.6, 25-29'' 12.8, 30-34'' 12.5, 35-40'' 13.2 (1/3 quantile on clean-book '
       'obs_version 6-7 rows, frozen 2026-10-12, never refitted); Over book two-sided, spread <= 6pp, ask depth >= $25, ask <= 0.85; '
       'Under book spread <= 6pp, ask depth >= $25, ask <= 0.85; score not contradicted by the ladder. No odds filter. Taker only. '
       'Settled on PM''s resolution of the Under token. '
       'VERDICT, forward rows only: (1) separation: Under net yield, quiet (bottom third) vs pressing (top third), first row per '
       'fixture on observation rows, CI clear of zero (~1,000 fixtures a side for the ~9pp seen); (2) money: net yield CI clear '
       'of zero after the fee (~10,000 bets to resolve +2% at odds ~2.0).',
       'The Over side of this market is rich (-3.3pp vs the realised rate) and the Under fair (+0.03pp), on 1,379 fixtures. '
       'The market moves the price with pressure by less than the goal rate moves (s17 review 2026-10-11), so quiet games '
       'should be where the Under is cheapest. Exploratory and in-sample: quiet games +0.9% CI[-8,+10]; +4.1% with this bar '
       'on the rows it was fitted on. Prior from July (236 games, prices-history mid): fade the over, +9% after a 4pp '
       'execution haircut, CI[-11,+29].',
       'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-HT-UNDER-QUIET —%');

INSERT INTO strategies (hypothesis_id, name, source, owner_id, theory, interpretation, rules, run_status, promoted_at)
SELECT h.id, 'Low Pressure HT Under 0.5', 'agent',
       (SELECT owner_id FROM strategies WHERE id = 17),
       'In a quiet first half the Under 0.5 is not overpriced the way the Over is.',
       'Back no goal before half time when it is 0-0 between 15'' and 40'' and the match is in the bottom third of pressure for that minute. 1u at the Polymarket ask, paper.',
       '{"phase": "in-play", "venue": "polymarket", "market": "1st Half O/U 0.5 (Under)", '
       '"execution": "taker at the Under''s own ask", "stake_u": 1, "self_settling": true, "rule_version": 1, '
       '"minutes": [15, 40], "score": "0-0", '
       '"pressure_ceiling": {"15-19": 11.4, "20-24": 13.6, "25-29": 12.8, "30-34": 12.5, "35-40": 13.2}, '
       '"odds_filter": "none", '
       '"book": {"max_spread": 0.06, "two_sided": true, "min_ask_depth_usd": 25, "max_ask": 0.85}, '
       '"rides_on": 17}'::jsonb,
       'running', now()
  FROM research_hypotheses h
 WHERE h.title LIKE 'H-HT-UNDER-QUIET —%'
   AND NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Low Pressure HT Under 0.5');

-- s17 is archived the same day, by the operator's decision: it passed its n >= 200
-- gate and failed it (308 bets, -11.0% net, CI[-22,+0]). From this code on,
-- ht_pressure_agent.strategy_id() returns None for a retired strategy and the arm
-- observes without buying, because the Under arm decides on its rows.
UPDATE strategies
   SET retired_at = now(), run_status = 'paused', is_public = false,
       retirement_reason = 'archived 2026-10-12 by its owner: failed its own verdict gate '
                           '(308 bets, -11.0% net, CI[-22,+0]). Still observing; its rows '
                           'feed Low Pressure HT Under 0.5 (db/076).'
 WHERE id = 17 AND retired_at IS NULL;

COMMIT;

-- One Under entry per fixture, the backstop under open_trades' own check.
-- CONCURRENTLY, outside the transaction: the daemons write to this table every
-- minute and a plain build would block them.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_ht_under_one_entry
    ON ht_pressure_observations (fixture_id) WHERE under_entered;
