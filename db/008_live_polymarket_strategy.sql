-- 008: "Live Polymarket" strategy — aggregated view of all on-chain bets.
--
-- This is a SYNTHETIC strategy (id=9). No trade has strategy_id=9 directly;
-- instead, v_strategy_performance UNIONs a row that aggregates every
-- paper_trade where pm_live=TRUE, regardless of its original strategy.
--
-- The site exposes this row as "Live Polymarket" in the leaderboard and,
-- when clicked, lists trades filtered by pm_live=TRUE (rather than by
-- strategy_id=9). Manual bets discovered on-chain that don't match any
-- existing paper_trade get inserted with strategy_id=9 by the sync script.

-- 1) Strategy row. hypothesis_id is NOT NULL; we synthesize a "meta" hypothesis
--    just to satisfy the FK — this strategy doesn't come from a single
--    research question, it aggregates every live position on the wallet.
INSERT INTO research_hypotheses (id, title, description, source, status, created_by)
VALUES (14,
        'Live Polymarket',
        'Aggregated view of all on-chain bets for the trading wallet 0x4fE6F5E78093925fa2D446c26b59cAA71558BB5c. Not a research hypothesis; this row exists solely to satisfy the strategies.hypothesis_id NOT NULL constraint.',
        'manual', 'promoted', 'system')
ON CONFLICT (id) DO NOTHING;

INSERT INTO strategies (id, name, promoted_at, hypothesis_id, rules)
VALUES (9, 'Live Polymarket', NOW(), 14,
        '{"phase": "live", "venue": "Polymarket CLOB",
          "wallet": "0x4fE6F5E78093925fa2D446c26b59cAA71558BB5c",
          "description": "Aggregated view of every paper_trade with pm_live=true plus any on-chain position discovered via PM data API + Polygon CTF balanceOf."}'::jsonb)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    rules = EXCLUDED.rules;

-- Keep the sequence aligned so future inserts don't collide on id=9.
SELECT setval('strategies_id_seq', GREATEST((SELECT MAX(id) FROM strategies), 9));

-- 2) Rebuild v_strategy_performance to include the synthetic "Live" row.
DROP VIEW IF EXISTS v_strategy_performance;

CREATE OR REPLACE VIEW v_strategy_performance AS
WITH per_strategy AS (
    SELECT
        s.id                                            AS strategy_id,
        s.name                                          AS strategy_name,
        CASE WHEN s.retired_at IS NULL THEN 'active' ELSE 'retired' END AS status,
        s.promoted_at,
        s.retired_at,
        s.retirement_reason,
        COUNT(pt.id)                                    AS n_trades,
        COUNT(*) FILTER (WHERE pt.result = 'won')       AS n_wins,
        COUNT(*) FILTER (WHERE pt.result = 'lost')      AS n_losses,
        COUNT(*) FILTER (WHERE pt.result = 'void')      AS n_voids,
        COUNT(*) FILTER (WHERE pt.result IS NULL)       AS n_open,
        COALESCE(SUM(pt.stake_units)
                 FILTER (WHERE pt.result IN ('won','lost')), 0)   AS total_staked,
        COALESCE(SUM(COALESCE(pt.payout_units, 0) - pt.stake_units)
                 FILTER (WHERE pt.result IN ('won','lost')), 0)   AS pl_units,
        AVG(pt.clv) FILTER (WHERE pt.clv IS NOT NULL)   AS avg_clv,
        AVG(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
        MIN(pt.placed_at)                               AS first_pick_at,
        MAX(pt.placed_at)                               AS latest_pick_at
    FROM strategies s
    LEFT JOIN paper_trades pt ON pt.strategy_id = s.id
    WHERE s.id <> 9              -- the Live row is built by aggregation below
    GROUP BY s.id, s.name, s.retired_at, s.retirement_reason, s.promoted_at
),
live_aggregate AS (
    SELECT
        9                                               AS strategy_id,
        'Live Polymarket'                               AS strategy_name,
        'active'                                        AS status,
        (SELECT promoted_at FROM strategies WHERE id = 9) AS promoted_at,
        NULL::timestamptz                               AS retired_at,
        NULL::text                                      AS retirement_reason,
        COUNT(pt.id)                                    AS n_trades,
        COUNT(*) FILTER (WHERE pt.result = 'won')       AS n_wins,
        COUNT(*) FILTER (WHERE pt.result = 'lost')      AS n_losses,
        COUNT(*) FILTER (WHERE pt.result = 'void')      AS n_voids,
        COUNT(*) FILTER (WHERE pt.result IS NULL)       AS n_open,
        COALESCE(SUM(pt.stake_units)
                 FILTER (WHERE pt.result IN ('won','lost')), 0)   AS total_staked,
        COALESCE(SUM(COALESCE(pt.payout_units, 0) - pt.stake_units)
                 FILTER (WHERE pt.result IN ('won','lost')), 0)   AS pl_units,
        AVG(pt.clv) FILTER (WHERE pt.clv IS NOT NULL)   AS avg_clv,
        AVG(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
        MIN(pt.placed_at)                               AS first_pick_at,
        MAX(pt.placed_at)                               AS latest_pick_at
    FROM paper_trades pt
    WHERE pt.pm_live = TRUE
)
SELECT *,
       CASE WHEN total_staked > 0
            THEN ROUND(100.0 * pl_units / total_staked, 4)
            ELSE NULL
       END AS yield_pct
FROM per_strategy
UNION ALL
SELECT *,
       CASE WHEN total_staked > 0
            THEN ROUND(100.0 * pl_units / total_staked, 4)
            ELSE NULL
       END AS yield_pct
FROM live_aggregate;

COMMENT ON VIEW v_strategy_performance IS
  'Per-strategy leaderboard + a synthetic id=9 "Live Polymarket" row that aggregates every pm_live=true trade.';
