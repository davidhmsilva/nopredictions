-- 091_ht_under_v2.sql
--
-- Low Pressure HT Under 0.5 v2: the first-half Under in a goal game that has
-- STAYED quiet. By the operator's decision, 2026-11-10. Agent:
-- ht_under_v2_agent.py, riding on the first-half rows inside pressure_agent.py,
-- next to version 1 (db/076), which keeps running unchanged as the comparison.
--
-- Version 1 stands at 330 settled, 44.2% won at a mean price of 48.6%, -10.5%
-- net. 126 of those entries were made on the fixture's first pressure reading.
--
-- Apply BEFORE deploying the code: ht.write() names the new columns.

BEGIN;
SET LOCAL lock_timeout = '10s';

-- Metadata-only on Postgres 11+ (constant defaults), so the lock is brief.
ALTER TABLE ht_pressure_observations
    ADD COLUMN IF NOT EXISTS under2_streak         smallint,
    ADD COLUMN IF NOT EXISTS under2_ask            real,
    ADD COLUMN IF NOT EXISTS under2_would_enter    boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS under2_entered        boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS under2_paper_trade_id integer,
    ADD COLUMN IF NOT EXISTS under2_skip_reason    text;

COMMENT ON COLUMN ht_pressure_observations.under2_streak IS
  'Consecutive polls, this one included, with pressure_now below the frozen ceiling for the minute (db/091). In memory: a daemon restart starts it again from 0.';

INSERT INTO research_hypotheses (title, description, rationale, source, status, created_by)
SELECT 'H-HT-UNDER-QUIET-V2 — when a goal game (Over 2.5 first seen at 0.52 or more) has stayed quiet for six polls and never pressed, PM''s 1st Half Under 0.5 is cheap',
       'Buy PM "1st Half O/U 0.5" Under at its own CLOB ask, 1u, paper, once per fixture, at the first poll where ALL hold: '
       '(1) every gate of H-HT-UNDER-QUIET on that poll (0-0; minute 15-40; pressure_now below the ceiling frozen 2026-10-12; '
       'Over and Under books two-sided, spread <= 6pp, ask depth >= $25, ask <= 0.85; score not contradicted); '
       '(2) this poll and the five before it all read below the ceiling for their minute (a poll with no reading breaks the run); '
       '(3) no reading of the fixture since 15'' reached 19; '
       '(4) pre_over25, the first Over 2.5 price seen for the fixture, >= 0.52. That price is read in play at the first poll, '
       'not before kick-off (pre_is_prematch true on 3 of 1,658 fixtures). '
       'Streak 6, peak 19 and 0.52 are frozen 2026-11-10 and never refitted. Taker only. Settled on PM''s resolution of the Under token. '
       'VERDICT, forward rows only (entries from the deploy on): net yield after the fee with its CI, read from 200 settled bets; '
       'under 200 it is a record, not a result. Archive if at n >= 200 the CI sits below zero. Version 1 on the same days is the comparison.',
       'The operator''s idea: a match the market priced for goals in which neither side is creating. '
       'Exploratory, measured 2026-11-09 on obs_version 6-7 rows, first qualifying row per fixture, price as 1 - (Over bid): '
       'sustained and never pressing, 256 fixtures, +4.4% net (se 6.0): 126 before 2026-10-12 at +8.2%, 130 after at +0.8%, '
       'where version 1 made -11.2% on the same days. With Over 2.5 >= 0.52: 96 fixtures, about +12%: 50 before at +16.5% (se 14.4), '
       '46 after at +6.6% (se 16.6). With Over 2.5 < 0.52: 160 fixtures, about 0%. '
       'The streak and the cut were chosen looking at these rows, one variant tested, so all of it is in-sample. '
       'What did NOT hold: Over 2.5 bands on version 1''s entries (>= 0.60: +11.9% before 2026-10-12, -37.3% after).',
       'user', 'pre_registered', 'claude'
WHERE NOT EXISTS (SELECT 1 FROM research_hypotheses WHERE title LIKE 'H-HT-UNDER-QUIET-V2 —%');

INSERT INTO strategies (hypothesis_id, name, source, owner_id, theory, interpretation, rules, run_status, promoted_at)
SELECT h.id, 'Low Pressure HT Under 0.5 v2', 'agent',
       (SELECT owner_id FROM strategies WHERE name = 'Low Pressure HT Under 0.5'),
       'When a goal game has stayed quiet, the first-half Under is still cheap.',
       'Back no goal before half time when it is 0-0 between 15'' and 40'', the match has been in the bottom third of pressure for six polls in a row and never pressed, and the Over 2.5 was first seen at 0.52 or more. 1u at the Polymarket ask, paper.',
       '{"phase": "in-play", "venue": "polymarket", "market": "1st Half O/U 0.5 (Under)", '
       '"execution": "taker at the Under''s own ask", "stake_u": 1, "self_settling": true, "rule_version": 2, '
       '"minutes": [15, 40], "score": "0-0", '
       '"pressure_ceiling": {"15-19": 11.4, "20-24": 13.6, "25-29": 12.8, "30-34": 12.5, "35-40": 13.2}, '
       '"quiet_streak_polls": 6, "peak_pressure_below": 19, '
       '"over25_first_seen_min": 0.52, "over25_read": "in play, first poll", '
       '"book": {"max_spread": 0.06, "two_sided": true, "min_ask_depth_usd": 25, "max_ask": 0.85}, '
       '"rides_on": 17, "compares_with": "Low Pressure HT Under 0.5"}'::jsonb,
       'running', now()
  FROM research_hypotheses h
 WHERE h.title LIKE 'H-HT-UNDER-QUIET-V2 —%'
   AND NOT EXISTS (SELECT 1 FROM strategies WHERE name = 'Low Pressure HT Under 0.5 v2');

COMMIT;

-- One v2 entry per fixture, the backstop under open_trades' own check.
-- CONCURRENTLY, outside the transaction: the daemons write to this table every
-- minute and a plain build would block them.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_ht_under2_one_entry
    ON ht_pressure_observations (fixture_id) WHERE under2_entered;
