-- =============================================================================
-- Football Prediction Markets Research Agent — schema
-- =============================================================================
-- Target: Supabase / Postgres 15+
--
-- Run order:
--   1. 001_schema.sql   (this file)
--   2. 002_seeds.sql    (leagues + bookmakers)
--
-- Conventions:
--   - Surrogate BIGSERIAL primary keys on all tables.
--   - External / source-specific identifiers live in alias tables or
--     string columns (never as primary keys), so the same underlying entity
--     can be joined across sources (FD, FBref, Polymarket, Kalshi).
--   - All timestamps are TIMESTAMPTZ. `kickoff_utc` is best-effort UTC —
--     Stage A stores local-league time interpreted as UTC (documented
--     limitation, adequate for date-bucketed backtests).
--   - Every ingestion writes a row into `data_ingestion_log` and every
--     hypothesis lives in `research_hypotheses` even if it fails — no
--     silent p-hacking.
-- =============================================================================

-- Keep re-runs safe-ish (only for dev resets; prod uses migrations).
-- Uncomment if you're rebuilding from scratch.
-- DROP SCHEMA public CASCADE;
-- CREATE SCHEMA public;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- -----------------------------------------------------------------------------
-- LEAGUES & SEASONS
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leagues (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT        NOT NULL UNIQUE,   -- our internal code, e.g. 'ENG-PR'
    name         TEXT        NOT NULL,          -- human name, e.g. 'Premier League'
    country      TEXT,                           -- ISO country name or 'INTL'
    tier         INTEGER,                        -- 1 = top flight, 2 = second, ...
    fd_code      TEXT,                           -- football-data.co.uk code (E0, SP1, ...)
    is_cup       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN leagues.code IS
    'Our canonical league code. Must match the LEAGUES dict in stage_a_football_data.py.';
COMMENT ON COLUMN leagues.fd_code IS
    'Football-Data.co.uk URL slug (E0, SP1, I1, ...). NULL for cups/intl not on FD.';

CREATE TABLE IF NOT EXISTS seasons (
    id           BIGSERIAL PRIMARY KEY,
    league_id    BIGINT      NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    label        TEXT        NOT NULL,           -- e.g. '2023-24'
    start_date   DATE        NOT NULL,
    end_date     DATE        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (league_id, label)
);

CREATE INDEX IF NOT EXISTS idx_seasons_label ON seasons(label);


-- -----------------------------------------------------------------------------
-- TEAMS & ALIASES
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS teams (
    id              BIGSERIAL PRIMARY KEY,
    canonical_name  TEXT        NOT NULL,
    country         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_teams_canonical_lower
    ON teams (LOWER(canonical_name));

-- Source-specific names (FD: "Nott'm Forest", FBref: "Nott'ham Forest",
-- Polymarket: "Nottingham Forest"). Resolution happens here, not in the
-- canonical teams table.
CREATE TABLE IF NOT EXISTS team_aliases (
    id          BIGSERIAL PRIMARY KEY,
    team_id     BIGINT      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    source      TEXT        NOT NULL,       -- 'football-data', 'fbref', 'polymarket', 'kalshi', ...
    alias       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, alias)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_team  ON team_aliases(team_id);
CREATE INDEX IF NOT EXISTS idx_team_aliases_lower ON team_aliases (source, LOWER(alias));


-- -----------------------------------------------------------------------------
-- BOOKMAKERS
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bookmakers (
    id          BIGSERIAL PRIMARY KEY,
    code        TEXT        NOT NULL UNIQUE,   -- internal short code ('B365', 'PSC', ...)
    name        TEXT        NOT NULL,
    kind        TEXT        NOT NULL,          -- 'bookmaker' | 'exchange' | 'aggregate'
    is_sharp    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN bookmakers.is_sharp IS
    'Pinnacle closing & Betfair Exchange are treated as sharp references.';


-- -----------------------------------------------------------------------------
-- MATCHES
-- -----------------------------------------------------------------------------

-- Status progression: scheduled -> live -> finished.
-- postponed / cancelled for the exceptions.
CREATE TABLE IF NOT EXISTS matches (
    id              BIGSERIAL PRIMARY KEY,
    season_id       BIGINT      NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    home_team_id    BIGINT      NOT NULL REFERENCES teams(id),
    away_team_id    BIGINT      NOT NULL REFERENCES teams(id),
    kickoff_utc     TIMESTAMPTZ NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'scheduled'
                     CHECK (status IN ('scheduled', 'live', 'finished', 'postponed', 'cancelled')),
    home_score      INTEGER,
    away_score      INTEGER,
    home_score_ht   INTEGER,
    away_score_ht   INTEGER,
    fd_source       TEXT,                      -- 'football-data', etc. (provenance of the row)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (season_id, home_team_id, away_team_id, kickoff_utc),
    CHECK (home_team_id <> away_team_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_kickoff       ON matches(kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_season        ON matches(season_id);
CREATE INDEX IF NOT EXISTS idx_matches_home          ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away          ON matches(away_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_status        ON matches(status);


-- -----------------------------------------------------------------------------
-- MATCH STATS (xG + box-score)
-- -----------------------------------------------------------------------------

-- Stage A fills the box-score columns from FD.
-- Stage B will fill home_xg / away_xg from FBref.
CREATE TABLE IF NOT EXISTS match_stats (
    match_id            BIGINT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    home_xg             NUMERIC(6, 3),
    away_xg             NUMERIC(6, 3),
    home_shots          INTEGER,
    away_shots          INTEGER,
    home_shots_on_tgt   INTEGER,
    away_shots_on_tgt   INTEGER,
    home_corners        INTEGER,
    away_corners        INTEGER,
    home_fouls          INTEGER,
    away_fouls          INTEGER,
    home_yellow         INTEGER,
    away_yellow         INTEGER,
    home_red            INTEGER,
    away_red            INTEGER,
    stats_source        TEXT        NOT NULL,   -- 'football-data' | 'fbref' | ...
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- MATCH ODDS
-- -----------------------------------------------------------------------------

-- One row = one (match, bookmaker, snapshot_type, observed_at) tuple.
-- snapshot_type: 'opening' / 'closing' / 'live'. FD gives us opening & closing;
-- Betfair exchange can be re-snapshotted at intervals later.
CREATE TABLE IF NOT EXISTS match_odds (
    id              BIGSERIAL PRIMARY KEY,
    match_id        BIGINT      NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker_id    BIGINT      NOT NULL REFERENCES bookmakers(id),
    snapshot_type   TEXT        NOT NULL
                    CHECK (snapshot_type IN ('opening', 'closing', 'live')),
    observed_at     TIMESTAMPTZ NOT NULL,
    home_odds       NUMERIC(8, 3),
    draw_odds       NUMERIC(8, 3),
    away_odds       NUMERIC(8, 3),
    over_2_5_odds   NUMERIC(8, 3),
    under_2_5_odds  NUMERIC(8, 3),
    ah_home_line    NUMERIC(4, 2),   -- placeholder for asian-handicap (not yet ingested)
    ah_home_odds    NUMERIC(8, 3),
    ah_away_odds    NUMERIC(8, 3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (match_id, bookmaker_id, snapshot_type, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_match_odds_match ON match_odds(match_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_book  ON match_odds(bookmaker_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_snap  ON match_odds(snapshot_type);


-- -----------------------------------------------------------------------------
-- PREDICTION MARKETS (Polymarket / Kalshi)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pm_markets (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT        NOT NULL,        -- 'polymarket' | 'kalshi'
    source_market_id    TEXT        NOT NULL,        -- vendor's market id / slug
    question            TEXT        NOT NULL,
    resolution_criteria TEXT,
    category            TEXT,                         -- 'football' always for this project
    match_id            BIGINT REFERENCES matches(id) ON DELETE SET NULL,
    market_type         TEXT,                         -- '1x2_home_win' | 'over_under_2_5' | 'total_goals' | ...
    outcome_label       TEXT,                         -- 'HOME' | 'DRAW' | 'AWAY' | 'OVER' | 'UNDER' | ...
    opened_at           TIMESTAMPTZ,
    closes_at           TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ,
    resolution_outcome  TEXT,
    liquidity_usd       NUMERIC(14, 2),
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_market_id)
);

CREATE INDEX IF NOT EXISTS idx_pm_markets_match      ON pm_markets(match_id);
CREATE INDEX IF NOT EXISTS idx_pm_markets_source    ON pm_markets(source);
CREATE INDEX IF NOT EXISTS idx_pm_markets_closes    ON pm_markets(closes_at);

CREATE TABLE IF NOT EXISTS pm_market_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    market_id         BIGINT      NOT NULL REFERENCES pm_markets(id) ON DELETE CASCADE,
    observed_at       TIMESTAMPTZ NOT NULL,
    yes_price         NUMERIC(10, 6),             -- probability, 0..1
    no_price          NUMERIC(10, 6),
    best_bid          NUMERIC(10, 6),
    best_ask          NUMERIC(10, 6),
    mid_price         NUMERIC(10, 6),
    volume_24h        NUMERIC(14, 2),
    open_interest     NUMERIC(14, 2),
    orderbook_snapshot JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (market_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_pm_snapshots_market ON pm_market_snapshots(market_id);
CREATE INDEX IF NOT EXISTS idx_pm_snapshots_time   ON pm_market_snapshots(observed_at);


-- -----------------------------------------------------------------------------
-- RESEARCH LAB NOTEBOOK
-- -----------------------------------------------------------------------------

-- Every hypothesis tested goes in here — even the ones that failed.
-- This is the primary defence against silent p-hacking.
CREATE TABLE IF NOT EXISTS research_hypotheses (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT        NOT NULL,
    description     TEXT,
    proposed_by     TEXT,                       -- 'agent:researcher' | 'agent:critic' | 'human'
    status          TEXT        NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'testing', 'rejected',
                                       'inconclusive', 'validated', 'promoted')),
    hypothesis_spec JSONB,                       -- machine-readable description of the rule
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON research_hypotheses(status);

-- Each execution of a backtest against a hypothesis.
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                 BIGSERIAL PRIMARY KEY,
    hypothesis_id      BIGINT      NOT NULL REFERENCES research_hypotheses(id) ON DELETE CASCADE,
    run_label          TEXT,
    train_start        DATE,
    train_end          DATE,
    test_start         DATE,
    test_end           DATE,
    n_selections       INTEGER     NOT NULL DEFAULT 0,
    n_wins             INTEGER,
    total_staked       NUMERIC(14, 4),
    total_returned     NUMERIC(14, 4),
    roi                NUMERIC(10, 6),
    yield_pct          NUMERIC(10, 6),
    clv                NUMERIC(10, 6),          -- average closing line value
    p_value_vs_zero    NUMERIC(10, 6),
    bookmaker_used     TEXT,
    config             JSONB,                    -- params used for the run
    result_summary     JSONB,                    -- full stats blob
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtests_hypothesis ON backtest_runs(hypothesis_id);

-- Hypotheses that cleared the bar (p<0.05, n>=200, positive CLV, passed critic).
CREATE TABLE IF NOT EXISTS strategies (
    id               BIGSERIAL PRIMARY KEY,
    hypothesis_id    BIGINT      NOT NULL REFERENCES research_hypotheses(id),
    name             TEXT        NOT NULL UNIQUE,
    version          INTEGER     NOT NULL DEFAULT 1,
    status           TEXT        NOT NULL DEFAULT 'paper'
                     CHECK (status IN ('paper', 'monitoring', 'retired')),
    config           JSONB,
    notes            TEXT,
    promoted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retired_at       TIMESTAMPTZ
);

-- Forward-test picks.
CREATE TABLE IF NOT EXISTS paper_trades (
    id                BIGSERIAL PRIMARY KEY,
    strategy_id       BIGINT REFERENCES strategies(id) ON DELETE SET NULL,
    hypothesis_id     BIGINT REFERENCES research_hypotheses(id) ON DELETE SET NULL,
    match_id          BIGINT REFERENCES matches(id) ON DELETE SET NULL,
    pm_market_id      BIGINT REFERENCES pm_markets(id) ON DELETE SET NULL,
    placed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    selection         TEXT        NOT NULL,         -- 'HOME' | 'DRAW' | 'AWAY' | 'OVER_2_5' | ...
    price_taken       NUMERIC(10, 6)  NOT NULL,
    closing_price     NUMERIC(10, 6),
    stake_units       NUMERIC(12, 4)  NOT NULL,
    kelly_fraction    NUMERIC(10, 6),
    agent_reasoning   TEXT,                          -- what the agent wrote when picking
    critic_notes      TEXT,                          -- what the critic said
    result            TEXT
                      CHECK (result IN ('pending', 'won', 'lost', 'void', 'cashed_out')),
    payout_units      NUMERIC(12, 4),
    clv               NUMERIC(10, 6),
    resolved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_result   ON paper_trades(result);
CREATE INDEX IF NOT EXISTS idx_paper_trades_placed   ON paper_trades(placed_at);


-- -----------------------------------------------------------------------------
-- AGENT OPERATIONS
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT        NOT NULL,       -- 'orchestrator' | 'researcher' | 'critic' | ...
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
    input_summary   TEXT,
    output_summary  TEXT,
    tokens_used     INTEGER,
    cost_usd        NUMERIC(10, 4),
    error_message   TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_name   ON agent_runs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);


-- -----------------------------------------------------------------------------
-- INGESTION LOG
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_ingestion_log (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,       -- 'football-data' | 'fbref' | 'polymarket' | 'kalshi'
    resource        TEXT        NOT NULL,       -- e.g. '2023-24/E0'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    rows_ingested   INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_source_resource
    ON data_ingestion_log(source, resource);
CREATE INDEX IF NOT EXISTS idx_ingestion_status
    ON data_ingestion_log(status);
