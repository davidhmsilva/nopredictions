-- 051_nfl_agent.sql — the NFL every-game agent (paper).
--
-- One row per priced candidate per sharp snapshot: every Polymarket full-game
-- moneyline / spread / total token the agent could have bought, with the fair
-- value it read off the sharp line and the net EV at the executable ask. The
-- trade itself goes to paper_trades like every other agent; this table is what
-- lets the choice be audited ("was the bet it made the best one on the board,
-- and was the board ever positive?").
--
-- RLS on, no policies: nothing here is for the client (db/045, db/048).

CREATE TABLE IF NOT EXISTS nfl_candidates (
    id              bigserial PRIMARY KEY,
    observed_at     timestamptz NOT NULL DEFAULT now(),
    obs_version     smallint    NOT NULL DEFAULT 1,
    game_slug       text        NOT NULL,
    kickoff         timestamptz NOT NULL,
    minutes_to_ko   numeric,
    home_team       text,
    away_team       text,
    market_type     text        NOT NULL,         -- moneyline | spreads | totals
    line            numeric,                      -- the token's own line (team -x → -x)
    side            text        NOT NULL,         -- team name, 'Over' or 'Under'
    condition_id    text        NOT NULL,
    token_id        text        NOT NULL,
    pm_bid          numeric,
    pm_ask          numeric,
    pm_liquidity    numeric,
    fair            numeric,                      -- token value from the sharp line
    fair_source     text,                         -- sharp_exact | sharp_model
    fair_book       text,                         -- pinnacle | consensus
    anchor_mu       numeric,                      -- expected margin, home team
    anchor_total    numeric,
    anchor_disagree numeric,                      -- spread- vs ML-implied margin, points
    fee_pp          numeric,
    ev_pct          numeric,                      -- net of fee and haircut, % of cash staked
    odds_snapshot_at timestamptz,
    chosen          boolean     NOT NULL DEFAULT false,
    paper_trade_id  integer REFERENCES paper_trades(id)
);

CREATE INDEX IF NOT EXISTS nfl_candidates_game_idx ON nfl_candidates (game_slug, observed_at);

ALTER TABLE nfl_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON nfl_candidates FROM anon, authenticated;
REVOKE ALL ON SEQUENCE nfl_candidates_id_seq FROM anon, authenticated;
