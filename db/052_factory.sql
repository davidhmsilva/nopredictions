-- 052_factory.sql — the strategy factory (agent/factory/).
--
-- factory_strategies holds only specs that passed out of sample (the full grid of
-- every run goes to reports/factory_grid_<date>.csv — thousands of rows that are
-- mostly noise do not belong in the database). Status:
--   candidate → paper → promoted | retired
-- `promoted` is a shortlist for real money, which is a person's decision.
--
-- factory_trades is one paper entry per strategy per fixture, taken from a row of
-- the live tape (source_table, source_row_id) at that row's CLOB ask.
--
-- RLS on, no policies, nothing granted to the client (db/045, db/048).

CREATE TABLE IF NOT EXISTS factory_strategies (
    id                serial PRIMARY KEY,
    spec_hash         text        NOT NULL UNIQUE,
    universe          text        NOT NULL,
    name              text        NOT NULL,
    template          text,
    spec              jsonb       NOT NULL,
    status            text        NOT NULL DEFAULT 'candidate'
                      CHECK (status IN ('candidate', 'paper', 'promoted', 'retired')),
    status_reason     text,
    status_changed_at timestamptz NOT NULL DEFAULT now(),
    bt                jsonb,
    bt_at             timestamptz,
    fwd_n             integer     NOT NULL DEFAULT 0,
    fwd_won           integer     NOT NULL DEFAULT 0,
    fwd_stake         numeric     NOT NULL DEFAULT 0,
    fwd_pnl           numeric     NOT NULL DEFAULT 0,
    fwd_yield         numeric,
    fwd_ci_lo         numeric,
    fwd_ci_hi         numeric,
    fwd_updated_at    timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS factory_trades (
    id             bigserial   PRIMARY KEY,
    strategy_id    integer     NOT NULL REFERENCES factory_strategies(id),
    fixture        text        NOT NULL,
    universe       text        NOT NULL,
    source_table   text,
    source_row_id  bigint,
    token_id       text,
    condition_id   text,
    observed_at    timestamptz,             -- when the tape row was recorded
    entry_at       timestamptz NOT NULL DEFAULT now(),
    entry_minute   integer,
    entry_ask      numeric     NOT NULL CHECK (entry_ask > 0 AND entry_ask < 1),
    entry_odds     numeric,
    stake          numeric     NOT NULL,
    fee_units      numeric,
    label          text,
    result         text,
    ret            numeric,                 -- per unit staked, net of the taker fee
    pnl            numeric,
    settled_at     timestamptz,
    UNIQUE (strategy_id, fixture)
);

CREATE INDEX IF NOT EXISTS factory_trades_pending_idx ON factory_trades (entry_at) WHERE settled_at IS NULL;
CREATE INDEX IF NOT EXISTS factory_strategies_status_idx ON factory_strategies (status);

ALTER TABLE factory_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE factory_trades ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON factory_strategies, factory_trades FROM anon, authenticated;
REVOKE ALL ON SEQUENCE factory_strategies_id_seq, factory_trades_id_seq FROM anon, authenticated;
