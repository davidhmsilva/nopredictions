-- =============================================================================
-- Migration 004 — Phase 1: PM-vs-sharp consensus tracking (v3)
-- =============================================================================
-- v3: Full reconciliation with deployed schema. The deployed Supabase schema
-- has evolved beyond db/001_schema.sql in repo across multiple tables.
--
-- Confirmed deployed names used here:
--   paper_trades:  market_id, outcome, entry_price, entry_odds, reasoning,
--                  critic_assessment, model_probability, expected_edge,
--                  confidence, match_odds_id (audit linkage to sharp odds)
--   pm_markets:    platform, external_id, title, raw_metadata
--                  (NO outcome_label — parent market only)
--   pm_market_snapshots: outcome, price (single-outcome row per snapshot)
--   match_odds:    snapshot_type, observed_at, *_odds, btts_yes_odds, etc
--
-- This migration adds 7 columns + 2 indexes + 3 views. Idempotent in any state
-- (fresh deploy / partial v1 / partial v2).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Cleanup from v1 partial run
-- -----------------------------------------------------------------------------
ALTER TABLE paper_trades DROP COLUMN IF EXISTS edge_pct;


-- -----------------------------------------------------------------------------
-- paper_trades: sharp consensus snapshot + signed timestamp + Twitter tracking
-- -----------------------------------------------------------------------------

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS sharp_consensus_price        NUMERIC(10, 6),
  ADD COLUMN IF NOT EXISTS sharp_consensus_sources      JSONB,
  ADD COLUMN IF NOT EXISTS signed_timestamp_proof       TEXT,
  ADD COLUMN IF NOT EXISTS twitter_post_id              TEXT,
  ADD COLUMN IF NOT EXISTS twitter_posted_at            TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS twitter_resolution_post_id   TEXT,
  ADD COLUMN IF NOT EXISTS twitter_resolution_posted_at TIMESTAMPTZ;

COMMENT ON COLUMN paper_trades.sharp_consensus_price IS
  'Computed fair decimal odds from Pinnacle + Betfair Exchange at pick time. Inverse of weighted sharp implied probability.';
COMMENT ON COLUMN paper_trades.sharp_consensus_sources IS
  'Audit trail. Example: {"pinnacle":{"odds":1.85,"weight":0.6},"betfair_ex_eu":{"odds":1.83,"weight":0.4}}';
COMMENT ON COLUMN paper_trades.signed_timestamp_proof IS
  'ED25519 signature over (id, placed_at, match_id, outcome, entry_price, sharp_consensus_price). Public key on site + Twitter bio.';


-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_paper_trades_twitter_pending
  ON paper_trades (placed_at)
  WHERE twitter_post_id IS NULL
    AND (result IS NULL OR result = 'pending');

CREATE INDEX IF NOT EXISTS idx_paper_trades_expected_edge
  ON paper_trades (expected_edge DESC NULLS LAST);


-- -----------------------------------------------------------------------------
-- VIEWS: drop any pre-existing definitions before recreating
-- -----------------------------------------------------------------------------
-- CREATE OR REPLACE VIEW does not allow renaming columns. If any of these
-- views already exist with different column shape (from earlier project work
-- or partial migration runs), CREATE OR REPLACE will fail. Dropping first
-- guarantees we land in a clean state. No CASCADE — if anything outside this
-- migration depends on these views, surface the dependency loudly.

DROP VIEW IF EXISTS v_open_paper_trades;
DROP VIEW IF EXISTS v_settled_paper_trades;
DROP VIEW IF EXISTS v_strategy_performance;


-- -----------------------------------------------------------------------------
-- VIEW: v_open_paper_trades — picks not yet resolved
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_open_paper_trades AS
SELECT
    pt.id,
    pt.strategy_id,
    s.name                              AS strategy_name,
    pt.match_id,
    m.kickoff_utc,
    home_t.canonical_name               AS home_team,
    away_t.canonical_name               AS away_team,
    l.code                              AS league_code,
    pt.market_id,
    pmm.platform                        AS market_platform,
    pmm.external_id                     AS pm_external_id,
    pmm.title                           AS market_title,
    pmm.market_type,
    pt.outcome,
    pt.entry_price                      AS pm_price,
    pt.entry_odds                       AS pm_odds,
    pt.model_probability,
    pt.sharp_consensus_price,
    pt.expected_edge,
    pt.confidence,
    pt.stake_units,
    pt.reasoning,
    pt.placed_at,
    pt.signed_timestamp_proof,
    pt.twitter_post_id,
    pt.twitter_posted_at
FROM paper_trades pt
LEFT JOIN strategies   s      ON s.id      = pt.strategy_id
LEFT JOIN matches      m      ON m.id      = pt.match_id
LEFT JOIN teams        home_t ON home_t.id = m.home_team_id
LEFT JOIN teams        away_t ON away_t.id = m.away_team_id
LEFT JOIN seasons      ss     ON ss.id     = m.season_id
LEFT JOIN leagues      l      ON l.id      = ss.league_id
LEFT JOIN pm_markets   pmm    ON pmm.id    = pt.market_id
WHERE pt.result IS NULL OR pt.result = 'pending'
ORDER BY m.kickoff_utc NULLS LAST, pt.placed_at;

COMMENT ON VIEW v_open_paper_trades IS
  'Open paper trades with full context. Powers site widget + Twitter poster + agent checks.';


-- -----------------------------------------------------------------------------
-- VIEW: v_settled_paper_trades — resolved picks with P&L
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_settled_paper_trades AS
SELECT
    pt.id,
    pt.strategy_id,
    s.name                              AS strategy_name,
    pt.match_id,
    m.kickoff_utc,
    home_t.canonical_name               AS home_team,
    away_t.canonical_name               AS away_team,
    pt.outcome,
    pt.entry_price                      AS pm_price,
    pt.entry_odds                       AS pm_odds,
    pt.sharp_consensus_price,
    pt.expected_edge,
    pt.stake_units,
    pt.result,
    pt.payout_units,
    (COALESCE(pt.payout_units, 0) - pt.stake_units) AS pl_units,
    pt.clv,
    pt.placed_at,
    pt.resolved_at
FROM paper_trades pt
LEFT JOIN strategies s      ON s.id      = pt.strategy_id
LEFT JOIN matches    m      ON m.id      = pt.match_id
LEFT JOIN teams      home_t ON home_t.id = m.home_team_id
LEFT JOIN teams      away_t ON away_t.id = m.away_team_id
WHERE pt.result IN ('won', 'lost', 'void', 'cashed_out')
ORDER BY pt.resolved_at DESC NULLS LAST;


-- -----------------------------------------------------------------------------
-- VIEW: v_strategy_performance — leaderboard
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_strategy_performance AS
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
    COUNT(*) FILTER (WHERE pt.result IS NULL OR pt.result = 'pending') AS n_open,
    COALESCE(SUM(pt.stake_units)
             FILTER (WHERE pt.result IN ('won','lost')), 0)   AS total_staked,
    COALESCE(SUM(COALESCE(pt.payout_units, 0) - pt.stake_units)
             FILTER (WHERE pt.result IN ('won','lost')), 0)   AS pl_units,
    CASE
      WHEN COALESCE(SUM(pt.stake_units)
                    FILTER (WHERE pt.result IN ('won','lost')), 0) > 0
      THEN ROUND(
        100.0 * SUM(COALESCE(pt.payout_units, 0) - pt.stake_units)
                FILTER (WHERE pt.result IN ('won','lost'))
              / SUM(pt.stake_units)
                FILTER (WHERE pt.result IN ('won','lost'))
        , 4)
      ELSE NULL
    END                                             AS yield_pct,
    AVG(pt.clv) FILTER (WHERE pt.clv IS NOT NULL)   AS avg_clv,
    AVG(pt.expected_edge) FILTER (WHERE pt.expected_edge IS NOT NULL) AS avg_edge_at_pick,
    MIN(pt.placed_at)                               AS first_pick_at,
    MAX(pt.placed_at)                               AS latest_pick_at
FROM strategies s
LEFT JOIN paper_trades pt ON pt.strategy_id = s.id
GROUP BY s.id, s.name, s.retired_at, s.retirement_reason, s.promoted_at;

COMMENT ON VIEW v_strategy_performance IS
  'Leaderboard: per-strategy trade counts, P&L units, yield %, avg CLV, avg edge at pick.';
