-- 064_lab_inplay.sql — live Lab rules (in-play agents users build) + the fix
-- that lets the Lab save an agent at all.
--
-- 1. strategies.hypothesis_id was NOT NULL, and saveLabAgent (site/app/lib/
--    agents.ts) never sends one: every "SAVE AS AN AGENT" since db/049 failed
--    with a not-null violation. A user's theory is not an entry in the
--    operator's research log, so the column is relaxed for Lab agents only —
--    an operator ('agent') strategy still has to name its hypothesis.
--
-- 2. lab_inplay_positions: one row per position a live Lab rule opens,
--    written by agent/lab_inplay_runner.py on the pressure poll. The position
--    carries its own exit state so a daemon restart mid-match keeps it.

ALTER TABLE strategies ALTER COLUMN hypothesis_id DROP NOT NULL;
DO $$ BEGIN
    ALTER TABLE strategies ADD CONSTRAINT strategies_hypothesis_unless_lab
        CHECK (source = 'lab' OR hypothesis_id IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS lab_inplay_positions (
    id                 BIGSERIAL PRIMARY KEY,
    strategy_id        INTEGER     NOT NULL REFERENCES strategies(id),
    paper_trade_id     INTEGER     REFERENCES paper_trades(id),
    spec               JSONB       NOT NULL,        -- the rule as it was when the position opened
    fixture_id         BIGINT      NOT NULL,
    league             TEXT,
    home               TEXT        NOT NULL,
    away               TEXT        NOT NULL,
    event_title        TEXT,
    market             TEXT        NOT NULL CHECK (market IN ('win', 'draw', 'next_goal')),
    team_side          TEXT        CHECK (team_side IN ('home', 'away')),
    team_name          TEXT,
    ko_price           NUMERIC(6,4),
    token_id           TEXT        NOT NULL,
    condition_id       TEXT,
    target_line        NUMERIC(4,1),               -- next_goal: the over line bought
    -- entry
    entered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_minute       INTEGER     NOT NULL,
    entry_home_goals   INTEGER     NOT NULL,
    entry_away_goals   INTEGER     NOT NULL,
    entry_ask          NUMERIC(6,4) NOT NULL,
    entry_bid          NUMERIC(6,4),
    home_pressure      NUMERIC(6,2),
    away_pressure      NUMERIC(6,2),
    stats_source       TEXT,
    shares             NUMERIC(14,6) NOT NULL,
    fee_rate           NUMERIC(6,4) NOT NULL,
    -- exit state
    status             TEXT        NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'goal_pending', 'sold', 'settled')),
    goal_seen_at       TIMESTAMPTZ,
    goal_minute        INTEGER,
    goal_home_goals    INTEGER,
    goal_away_goals    INTEGER,
    goals_reversed     INTEGER     NOT NULL DEFAULT 0,
    last_seen_at       TIMESTAMPTZ,
    last_minute        INTEGER,
    exit_at            TIMESTAMPTZ,
    exit_minute        INTEGER,
    exit_bid           NUMERIC(6,4),
    exit_ask           NUMERIC(6,4),
    exit_reason        TEXT,        -- sold_after_goal | sold_at_minute | held_won | held_lost
    payout_units       NUMERIC(12,6),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lab_inplay_status ON lab_inplay_positions (status);
CREATE INDEX IF NOT EXISTS idx_lab_inplay_strategy ON lab_inplay_positions (strategy_id, entered_at);

ALTER TABLE lab_inplay_positions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE lab_inplay_positions FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE lab_inplay_positions FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
