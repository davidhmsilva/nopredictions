-- 069_strategy_performance_n_open.sql
--
-- n_open counted the NULL row of a LEFT JOIN as an open bet.
--
-- per_strategy reads `strategies s LEFT JOIN paper_trades pt`, and n_open was
-- `count(*) FILTER (WHERE pt.result IS NULL)`. A strategy with no trades at
-- all still gets one joined row, with every pt column NULL, and that row
-- passes `pt.result IS NULL`. So every agent with no bets read "1 open" — on
-- the site's agent cards (2026-10-05) and in anything else that reads this
-- view. Before this migration: 4 of 21 rows (strategies 14, 22, 23, 24),
-- n_trades = 0 and n_open = 1. The 17 rows with trades were already right.
--
-- n_wins / n_losses / n_voids were never affected: their filters need a
-- non-null result. n_trades already used count(pt.id).
--
-- count(pt.id) in all three CTEs, though only per_strategy has the LEFT JOIN:
-- in live_aggregate and pressure_v2 every row is a real trade, so the number
-- is the same, and one spelling means the next edit cannot copy the wrong one.
--
-- CREATE OR REPLACE, not DROP + CREATE: same columns and types (count() is
-- bigint either way), so the grants (db/048, db/050) and the COMMENT survive.
-- Nothing else changes; the body is pg_get_viewdef of the live view with the
-- three n_open expressions swapped. Rollback = swap them back.
--
-- ⚠️ v_agent_trades (db/054) mirrors this view's MEMBERSHIP rules, not its
-- counts, so it needs no change.

SET lock_timeout = '5s';

CREATE OR REPLACE VIEW public.v_strategy_performance AS
WITH per_strategy AS (
         SELECT s.id AS strategy_id,
            s.name AS strategy_name,
            NULL::integer AS parent_strategy_id,
                CASE
                    WHEN s.retired_at IS NULL THEN 'active'::text
                    ELSE 'retired'::text
                END AS status,
            s.promoted_at,
            s.retired_at,
            s.retirement_reason,
            count(pt.id) AS n_trades,
            count(*) FILTER (WHERE pt.result = 'won'::text) AS n_wins,
            count(*) FILTER (WHERE pt.result = 'lost'::text) AS n_losses,
            count(*) FILTER (WHERE pt.result = 'void'::text) AS n_voids,
            count(pt.id) FILTER (WHERE pt.result IS NULL) AS n_open,
            COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS total_staked,
            COALESCE(sum(COALESCE(pt.payout_units, 0::numeric) - pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS pl_units,
            avg(pt.clv) FILTER (WHERE pt.clv IS NOT NULL) AS avg_clv,
            avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
            min(pt.placed_at) AS first_pick_at,
            max(pt.placed_at) AS latest_pick_at
           FROM strategies s
             LEFT JOIN paper_trades pt ON pt.strategy_id = s.id
          WHERE s.id <> ALL (ARRAY[9, 19])
          GROUP BY s.id, s.name, s.retired_at, s.retirement_reason, s.promoted_at
        ), live_aggregate AS (
         SELECT 9 AS strategy_id,
            'Live Polymarket'::text AS strategy_name,
            NULL::integer AS parent_strategy_id,
            'active'::text AS status,
            ( SELECT strategies.promoted_at
                   FROM strategies
                  WHERE strategies.id = 9) AS promoted_at,
            NULL::timestamp with time zone AS retired_at,
            NULL::text AS retirement_reason,
            count(pt.id) AS n_trades,
            count(*) FILTER (WHERE pt.result = 'won'::text) AS n_wins,
            count(*) FILTER (WHERE pt.result = 'lost'::text) AS n_losses,
            count(*) FILTER (WHERE pt.result = 'void'::text) AS n_voids,
            count(pt.id) FILTER (WHERE pt.result IS NULL) AS n_open,
            COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS total_staked,
            COALESCE(sum(COALESCE(pt.payout_units, 0::numeric) - pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS pl_units,
            avg(pt.clv) FILTER (WHERE pt.clv IS NOT NULL) AS avg_clv,
            avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
            min(pt.placed_at) AS first_pick_at,
            max(pt.placed_at) AS latest_pick_at
           FROM paper_trades pt
          WHERE pt.pm_live = true
        ), pressure_v2 AS (
         SELECT 19 AS strategy_id,
            'Live Pressure Overs v2'::text AS strategy_name,
            16 AS parent_strategy_id,
            'active'::text AS status,
            ( SELECT strategies.promoted_at
                   FROM strategies
                  WHERE strategies.id = 19) AS promoted_at,
            NULL::timestamp with time zone AS retired_at,
            NULL::text AS retirement_reason,
            count(pt.id) AS n_trades,
            count(*) FILTER (WHERE pt.result = 'won'::text) AS n_wins,
            count(*) FILTER (WHERE pt.result = 'lost'::text) AS n_losses,
            count(*) FILTER (WHERE pt.result = 'void'::text) AS n_voids,
            count(pt.id) FILTER (WHERE pt.result IS NULL) AS n_open,
            COALESCE(sum(pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS total_staked,
            COALESCE(sum(COALESCE(pt.payout_units, 0::numeric) - pt.stake_units) FILTER (WHERE pt.result = ANY (ARRAY['won'::text, 'lost'::text])), 0::numeric) AS pl_units,
            avg(pt.clv) FILTER (WHERE pt.clv IS NOT NULL) AS avg_clv,
            avg(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
            min(pt.placed_at) AS first_pick_at,
            max(pt.placed_at) AS latest_pick_at
           FROM paper_trades pt
             JOIN pressure_observations po ON po.paper_trade_id = pt.id
          WHERE pt.strategy_id = 16 AND pt.placed_at >= '2026-09-05 17:50:00+00'::timestamp with time zone AND po.minute >= 75 AND po.minute <= 84 AND pt.entry_odds >= 2.00 AND pt.entry_odds < 3.00
        ), unioned AS (
         SELECT per_strategy.strategy_id,
            per_strategy.strategy_name,
            per_strategy.parent_strategy_id,
            per_strategy.status,
            per_strategy.promoted_at,
            per_strategy.retired_at,
            per_strategy.retirement_reason,
            per_strategy.n_trades,
            per_strategy.n_wins,
            per_strategy.n_losses,
            per_strategy.n_voids,
            per_strategy.n_open,
            per_strategy.total_staked,
            per_strategy.pl_units,
            per_strategy.avg_clv,
            per_strategy.avg_edge_at_pick,
            per_strategy.first_pick_at,
            per_strategy.latest_pick_at
           FROM per_strategy
        UNION ALL
         SELECT live_aggregate.strategy_id,
            live_aggregate.strategy_name,
            live_aggregate.parent_strategy_id,
            live_aggregate.status,
            live_aggregate.promoted_at,
            live_aggregate.retired_at,
            live_aggregate.retirement_reason,
            live_aggregate.n_trades,
            live_aggregate.n_wins,
            live_aggregate.n_losses,
            live_aggregate.n_voids,
            live_aggregate.n_open,
            live_aggregate.total_staked,
            live_aggregate.pl_units,
            live_aggregate.avg_clv,
            live_aggregate.avg_edge_at_pick,
            live_aggregate.first_pick_at,
            live_aggregate.latest_pick_at
           FROM live_aggregate
        UNION ALL
         SELECT pressure_v2.strategy_id,
            pressure_v2.strategy_name,
            pressure_v2.parent_strategy_id,
            pressure_v2.status,
            pressure_v2.promoted_at,
            pressure_v2.retired_at,
            pressure_v2.retirement_reason,
            pressure_v2.n_trades,
            pressure_v2.n_wins,
            pressure_v2.n_losses,
            pressure_v2.n_voids,
            pressure_v2.n_open,
            pressure_v2.total_staked,
            pressure_v2.pl_units,
            pressure_v2.avg_clv,
            pressure_v2.avg_edge_at_pick,
            pressure_v2.first_pick_at,
            pressure_v2.latest_pick_at
           FROM pressure_v2
        )
 SELECT strategy_id,
    strategy_name,
    parent_strategy_id,
    status,
    promoted_at,
    retired_at,
    retirement_reason,
    n_trades,
    n_wins,
    n_losses,
    n_voids,
    n_open,
    total_staked,
    pl_units,
    avg_clv,
    avg_edge_at_pick,
    first_pick_at,
    latest_pick_at,
        CASE
            WHEN total_staked > 0::numeric THEN round(100.0 * pl_units / total_staked, 4)
            ELSE NULL::numeric
        END AS yield_pct
   FROM unioned;
