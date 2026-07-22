-- Pre-match longshot drift positions.
-- Strategy: buy NO on longshots early (YES overpriced vs DC model), exit at +20%
-- before kickoff, capturing the systematic PM price drift toward sharper fair value.

CREATE TABLE IF NOT EXISTS drift_positions (
    id                  BIGSERIAL PRIMARY KEY,
    token_id            TEXT NOT NULL,          -- YES token id (we hold the NO side)
    condition_id        TEXT,
    question            TEXT,
    home                TEXT,
    away                TEXT,
    outcome_key         TEXT,                   -- which outcome is the longshot
    kickoff_at          TIMESTAMPTZ,
    strategy_id         INT REFERENCES strategies(id),

    -- Entry
    entry_at            TIMESTAMPTZ DEFAULT NOW(),
    entry_yes_price     NUMERIC NOT NULL,       -- YES price at entry (high, expected to fall)
    entry_no_price      NUMERIC NOT NULL,       -- = 1 - entry_yes_price
    dc_fair             NUMERIC,               -- DC model fair prob for YES
    edge_pp             NUMERIC,               -- (yes_price - dc_fair) * 100

    stake_usd           NUMERIC DEFAULT 1.0,
    size_shares         NUMERIC,               -- shares of NO held (= stake / entry_no_price)

    -- Exit targets
    target_no_price     NUMERIC,               -- NO price at which to take profit (+20%)
    stop_no_price       NUMERIC,               -- NO price at which to stop loss (-15%)
    hard_close_minutes  INT DEFAULT 30,        -- minutes before kickoff to force close

    -- Exit
    status              TEXT NOT NULL DEFAULT 'open',
    exit_at             TIMESTAMPTZ,
    exit_yes_price      NUMERIC,
    exit_no_price       NUMERIC,
    exit_reason         TEXT,                  -- target, stop, kickoff, manual
    realized_pnl_pct    NUMERIC,
    realized_pnl_usd    NUMERIC,

    -- Live money fields (same pattern as convergence_shadow)
    pm_live             BOOLEAN DEFAULT FALSE,
    pm_order_id_entry   TEXT,
    pm_live_size        NUMERIC,
    pm_live_stake_usd   NUMERIC,
    pm_exit_order_id    TEXT,
    pm_exit_price_actual NUMERIC,
    pm_live_pnl_usd     NUMERIC,

    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (token_id, status)                  -- one open position per token
);

CREATE INDEX IF NOT EXISTS idx_drift_status ON drift_positions (status);
CREATE INDEX IF NOT EXISTS idx_drift_kickoff ON drift_positions (kickoff_at);

-- Register strategy
INSERT INTO strategies (name, description, active)
VALUES ('Pre-Match Drift', 'Buy NO on longshots early pre-match; exit at +20% before kickoff capturing PM price drift', true)
ON CONFLICT (name) DO NOTHING;
