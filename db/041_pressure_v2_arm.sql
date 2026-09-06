-- 041_pressure_v2_arm.sql
--
-- Registers "Live Pressure Overs v2" — the minute 75-84 / odds 2.00-3.00
-- pocket, tracked forward on the public dashboard.
--
-- v2 IS NOT A SECOND AGENT. Nothing new is decided, nothing new is bought and
-- pressure_agent.py is not touched. Strategy 16 keeps entering exactly as it
-- does today; v2 is a READING of the same entries — the subset that happens to
-- fall inside the window. Both facts follow from that:
--
--   * no row is duplicated in paper_trades. v2 owns no trades of its own, so
--     its P&L is a slice of strategy 16's, never an addition to it. The view
--     therefore carries parent_strategy_id = 16 on the v2 row, and the site
--     leaves any row with a parent out of its cross-strategy totals — counting
--     it would book the same fixture twice.
--   * v2 can be changed or dropped without the agent noticing.
--
--
-- WHY THIS IS REGISTERED RATHER THAN SHIPPED AS A GATE
--
-- The window was chosen by looking at a 6x6 grid of minute x odds over 112
-- settled entries and taking the best cell. In sample it reads +15.31% net of
-- the taker fee (n=44, W=23, CI[-18.3,+48.9]) against -16.28% for the 68 it
-- drops. That number is not evidence:
--
--   Permutation test — reshuffle the 112 P&L outcomes and re-run the same
--   search for the best contiguous block with n>=30, 2000 draws:
--
--     best block, real data            +15.31% / bet
--     best block under the null, median +13.05%
--     best block under the null, p95    +29.29%
--     p-value                             0.390
--
--   Pure noise finds a +13% block half the time. The observed block sits at
--   the 61st percentile of nothing.
--
--   Calibration arm — the same pocket measured where the power actually is.
--   All pressure_observations rows with a clean book (spread <= 6pp, depth >=
--   $1000), minute 75-84, ask 0.333-0.499, `real - ask`, bootstrap clustered
--   by fixture:
--
--     564 fixtures / 2,021 rows      real - ask = +1.34pp  CI[-3.46, +6.28]
--     average taker fee                           2.89pp of stake
--     net                                        -1.55pp
--
--   So the prior for this arm is NOT the +15.31%. It is zero before the fee
--   and slightly negative after it. The honest expectation is that v2 tracks
--   strategy 16's subset and converges on the same nothing.
--
-- The window also selects the most fee-expensive band per unit staked: the
-- taker fee is shares * 0.05 * p * (1-p), i.e. 0.05 * (1-p) per unit at price
-- p, so 2.9pp at odds 2.5 against 1.0pp at odds 1.25.
--
--
-- FORWARD ONLY — THE CUTOFF IS THE WHOLE POINT
--
-- The filter is derivable from columns already stored on every entry, so the
-- view COULD backfill all 112 past trades and open at +15.31%. It deliberately
-- does not. A pre-registered test that shows its own discovery sample as a
-- track record is not a test, and a dashboard that opens on the in-sample
-- number teaches the reader to expect it. v2 starts at n=0 on 2026-09-05 and
-- only counts what strategy 16 enters from here.
--
-- Verdict gate, unchanged from the other arms: n >= 200 settled v2 entries AND
-- a yield CI clear of zero after the fee AND v2 beating the entries it drops
-- on the same period. Below that it is a line on a chart.

BEGIN;

-- ── the pre-registration ────────────────────────────────────────────────────

INSERT INTO research_hypotheses (id, title, description, rationale, source, status, created_by)
VALUES (
  33,
  'H-PRESSURE-V2 — the minute 75-84 / odds 2.00-3.00 pocket of Live Pressure Overs',
  'Among the entries strategy 16 already makes, those bought between minute 75 '
  'and 84 at decimal odds of 2.00 to 2.99 have positive yield net of the taker '
  'fee, and beat the entries outside that window over the same period. '
  'Population: every strategy 16 paper trade placed from 2026-09-05 onward '
  '(forward only — the discovery sample is excluded by the view). '
  'Primary test: yield with a bootstrap CI clustered by fixture, net of '
  'shares * 0.05 * p * (1-p). Secondary, and far higher powered: `real - ask` '
  'on all pressure_observations rows falling inside the window, entered or not. '
  'Gate: n >= 200 settled entries AND CI clear of zero after the fee AND v2 '
  'above the dropped complement.',
  'Chosen by taking the best cell of a 6x6 minute x odds grid over 112 settled '
  'entries (+15.31% net, n=44). A permutation test over the same grid puts the '
  'best block under pure noise at a median of +13.05% (p=0.390), and the '
  'calibration arm measures the pocket at +1.34pp CI[-3.46,+6.28] on 564 '
  'fixtures against a 2.89pp fee. The prior is therefore zero-to-negative; this '
  'is registered to be measured forward, not because it is believed.',
  'user',
  'pre_registered',
  'claude'
)
ON CONFLICT (id) DO NOTHING;

-- ── the arm ─────────────────────────────────────────────────────────────────
-- Owns no paper_trades. It exists so the dashboard has a name, a promoted_at
-- and a rules blob to render; every number it shows is computed by the view.

INSERT INTO strategies (id, hypothesis_id, name, rules, promoted_at)
VALUES (
  19,
  33,
  'Live Pressure Overs v2',
  jsonb_build_object(
    'derived_from',    16,
    'kind',            'filter over the parent arm — decides nothing, buys nothing',
    'entry_minute',    jsonb_build_object('min', 75, 'max', 84),
    'entry_odds',      jsonb_build_object('min', 2.00, 'max_exclusive', 3.00),
    'counts_from',     '2026-09-05T17:50:00Z',
    'forward_only',    true,
    'prior',           'zero to negative — see 041 header: permutation p=0.390, '
                       'calibration +1.34pp CI[-3.46,+6.28] vs a 2.89pp fee',
    'verdict_gate',    'n>=200 settled AND CI clear of zero after fee AND beats the dropped complement'
  ),
  TIMESTAMPTZ '2026-09-05 17:50:00+00'
)
ON CONFLICT (id) DO UPDATE
  SET hypothesis_id = EXCLUDED.hypothesis_id,
      name          = EXCLUDED.name,
      rules         = EXCLUDED.rules;

-- ── leaderboard ─────────────────────────────────────────────────────────────
-- Same shape as before plus parent_strategy_id, and a second synthetic
-- aggregate alongside the existing "Live Polymarket" one (db/008). Strategies
-- 9 and 19 are both excluded from per_strategy: neither has paper_trades of
-- its own, so the plain GROUP BY would render them as empty rows.

DROP VIEW IF EXISTS v_strategy_performance;

CREATE OR REPLACE VIEW v_strategy_performance AS
WITH per_strategy AS (
  SELECT s.id AS strategy_id,
         s.name AS strategy_name,
         NULL::int AS parent_strategy_id,
         CASE WHEN s.retired_at IS NULL THEN 'active' ELSE 'retired' END AS status,
         s.promoted_at,
         s.retired_at,
         s.retirement_reason,
         count(pt.id) AS n_trades,
         count(*) FILTER (WHERE pt.result = 'won')  AS n_wins,
         count(*) FILTER (WHERE pt.result = 'lost') AS n_losses,
         count(*) FILTER (WHERE pt.result = 'void') AS n_voids,
         count(*) FILTER (WHERE pt.result IS NULL)  AS n_open,
         COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result IN ('won','lost')), 0) AS total_staked,
         COALESCE(sum(COALESCE(pt.payout_units, 0) - pt.stake_units)
                  FILTER (WHERE pt.result IN ('won','lost')), 0) AS pl_units,
         avg(pt.clv)           FILTER (WHERE pt.clv IS NOT NULL)           AS avg_clv,
         avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
         min(pt.placed_at) AS first_pick_at,
         max(pt.placed_at) AS latest_pick_at
    FROM strategies s
    LEFT JOIN paper_trades pt ON pt.strategy_id = s.id
   WHERE s.id NOT IN (9, 19)
   GROUP BY s.id, s.name, s.retired_at, s.retirement_reason, s.promoted_at
),
live_aggregate AS (
  SELECT 9 AS strategy_id,
         'Live Polymarket'::text AS strategy_name,
         NULL::int AS parent_strategy_id,
         'active'::text AS status,
         (SELECT promoted_at FROM strategies WHERE id = 9) AS promoted_at,
         NULL::timestamptz AS retired_at,
         NULL::text AS retirement_reason,
         count(pt.id) AS n_trades,
         count(*) FILTER (WHERE pt.result = 'won')  AS n_wins,
         count(*) FILTER (WHERE pt.result = 'lost') AS n_losses,
         count(*) FILTER (WHERE pt.result = 'void') AS n_voids,
         count(*) FILTER (WHERE pt.result IS NULL)  AS n_open,
         COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result IN ('won','lost')), 0) AS total_staked,
         COALESCE(sum(COALESCE(pt.payout_units, 0) - pt.stake_units)
                  FILTER (WHERE pt.result IN ('won','lost')), 0) AS pl_units,
         avg(pt.clv)           FILTER (WHERE pt.clv IS NOT NULL)           AS avg_clv,
         avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
         min(pt.placed_at) AS first_pick_at,
         max(pt.placed_at) AS latest_pick_at
    FROM paper_trades pt
   WHERE pt.pm_live = true
),
-- v2: strategy 16's own entries, filtered. entry_odds is stored on the trade
-- and the clock is not, so the minute comes from the observation row that
-- opened it (paper_trade_id is unique there). An entry whose observation row
-- is missing cannot be placed in the window and is left out rather than
-- assumed in.
pressure_v2 AS (
  SELECT 19 AS strategy_id,
         'Live Pressure Overs v2'::text AS strategy_name,
         16 AS parent_strategy_id,
         'active'::text AS status,
         (SELECT promoted_at FROM strategies WHERE id = 19) AS promoted_at,
         NULL::timestamptz AS retired_at,
         NULL::text AS retirement_reason,
         count(pt.id) AS n_trades,
         count(*) FILTER (WHERE pt.result = 'won')  AS n_wins,
         count(*) FILTER (WHERE pt.result = 'lost') AS n_losses,
         count(*) FILTER (WHERE pt.result = 'void') AS n_voids,
         count(*) FILTER (WHERE pt.result IS NULL)  AS n_open,
         COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result IN ('won','lost')), 0) AS total_staked,
         COALESCE(sum(COALESCE(pt.payout_units, 0) - pt.stake_units)
                  FILTER (WHERE pt.result IN ('won','lost')), 0) AS pl_units,
         avg(pt.clv)           FILTER (WHERE pt.clv IS NOT NULL)           AS avg_clv,
         avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
         min(pt.placed_at) AS first_pick_at,
         max(pt.placed_at) AS latest_pick_at
    FROM paper_trades pt
    JOIN pressure_observations po ON po.paper_trade_id = pt.id
   WHERE pt.strategy_id = 16
     AND pt.placed_at >= TIMESTAMPTZ '2026-09-05 17:50:00+00'   -- forward only
     AND po.minute BETWEEN 75 AND 84
     AND pt.entry_odds >= 2.00
     AND pt.entry_odds <  3.00
),
unioned AS (
  SELECT * FROM per_strategy
  UNION ALL SELECT * FROM live_aggregate
  UNION ALL SELECT * FROM pressure_v2
)
SELECT strategy_id, strategy_name, parent_strategy_id, status, promoted_at,
       retired_at, retirement_reason, n_trades, n_wins, n_losses, n_voids,
       n_open, total_staked, pl_units, avg_clv, avg_edge_at_pick,
       first_pick_at, latest_pick_at,
       CASE WHEN total_staked > 0
            THEN round(100.0 * pl_units / total_staked, 4)
       END AS yield_pct
  FROM unioned;

COMMENT ON VIEW v_strategy_performance IS
  'Leaderboard. One row per strategy, plus two synthetic aggregates that own no '
  'paper_trades of their own: 9 (Live Polymarket — every on-chain trade, any '
  'source) and 19 (Live Pressure Overs v2 — strategy 16 entries at minute 75-84 '
  'and odds 2.00-2.99, placed 2026-09-05 onward). A row with parent_strategy_id '
  'set is a SLICE of that parent, so it must be excluded from any total summed '
  'across rows or the same trade is counted twice.';

COMMIT;
