-- 015_convergence_shadow.sql
-- Shadow ledger for the in-play CONVERGENCE strategy (buy an underpriced
-- state-driven outcome late, sell on convergence — flip, not hold-to-resolution).
--
-- This is deliberately SEPARATE from paper_trades: the convergence play is
-- gated differently from the model-vs-sharp strategies (no sharp line applies
-- in-play), and we want a clean, self-contained realized-flip-P&L track record
-- before any real money is armed. One row per (token, entry) — entry and exit
-- live in the same row.

CREATE TABLE IF NOT EXISTS convergence_shadow (
    id              BIGSERIAL PRIMARY KEY,

    -- identity
    token_id        TEXT NOT NULL,         -- PM YES clob token id
    condition_id    TEXT,
    question        TEXT,
    home            TEXT,
    away            TEXT,
    outcome_key     TEXT,                  -- draw / home_win / away_win
    play_type       TEXT,                  -- draw_tied / leader_ml
    fixture_id      BIGINT,                -- api-football fixture id (for settlement)

    -- entry
    entry_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entry_price     NUMERIC,               -- ask we cross (executable)
    entry_fair      NUMERIC,               -- sim fair at entry
    entry_edge_pp   NUMERIC,
    entry_minute    INT,
    entry_score     TEXT,
    stake_usd       NUMERIC DEFAULT 10.0,
    size_shares     NUMERIC,               -- stake_usd / entry_price

    -- exit
    status          TEXT NOT NULL DEFAULT 'open',   -- open / closed
    exit_at         TIMESTAMPTZ,
    exit_price      NUMERIC,
    exit_reason     TEXT,                  -- converged / settled_win / settled_loss / timeout
    exit_minute     INT,
    realized_pnl_usd NUMERIC,              -- (exit_price - entry_price) * size_shares
    realized_pct    NUMERIC,               -- exit_price/entry_price - 1

    -- bookkeeping
    last_fair       NUMERIC,               -- most recent recomputed fair
    last_bid        NUMERIC,
    last_checked_at TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- One open position per token at a time.
CREATE UNIQUE INDEX IF NOT EXISTS convergence_shadow_open_token
    ON convergence_shadow (token_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS convergence_shadow_status ON convergence_shadow (status);
CREATE INDEX IF NOT EXISTS convergence_shadow_fixture ON convergence_shadow (fixture_id);
