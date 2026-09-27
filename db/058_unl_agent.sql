-- 058_unl_agent.sql — the UEFA Nations League every-game agent (paper).
--
-- The soccer twin of nfl_candidates (db/051): one row per priced candidate per
-- sharp snapshot — every full-time Polymarket token the agent could have bought
-- (1X2 Yes/No, every goals line, every handicap, BTTS), with the fair value it
-- read off the sharp line and the net EV at the executable ask. The trade itself
-- goes to paper_trades like every other agent.
--
-- RLS on, no policies: nothing here is for the client (db/045, db/048).

CREATE TABLE IF NOT EXISTS unl_candidates (
    id               bigserial PRIMARY KEY,
    observed_at      timestamptz NOT NULL DEFAULT now(),
    obs_version      smallint    NOT NULL DEFAULT 1,
    game_slug        text        NOT NULL,
    kickoff          timestamptz NOT NULL,
    minutes_to_ko    numeric,
    home_team        text,                         -- oriented by the sharp feed when matched
    away_team        text,
    market_type      text        NOT NULL,         -- moneyline | totals | spreads | both_teams_to_score
    subject          text,                         -- moneyline: the team or 'Draw'
    line             numeric,                      -- spreads: the side's own line; totals: the line
    side             text        NOT NULL,         -- Yes/No | Over/Under | team name
    condition_id     text        NOT NULL,
    token_id         text        NOT NULL,
    pm_bid           numeric,
    pm_ask           numeric,
    pm_liquidity     numeric,
    fair             numeric,                      -- after haircut
    fair_raw         numeric,
    fair_source      text,                         -- sharp_exact | sharp_model | pm_mid
    fair_book        text,                         -- pinnacle | consensus(n)
    anchor_lh        numeric,                      -- Dixon-Coles λ home
    anchor_la        numeric,
    anchor_rho       numeric,
    anchor_resid_pp  numeric,                      -- worst miss on the book's own numbers
    sharp_total_line numeric,
    fee_pp           numeric,
    ev_pct           numeric,                      -- net of fee and haircut, % of cash staked
    odds_snapshot_at timestamptz,
    chosen           boolean     NOT NULL DEFAULT false,
    paper_trade_id   integer REFERENCES paper_trades(id)
);

CREATE INDEX IF NOT EXISTS unl_candidates_game_idx ON unl_candidates (game_slug, observed_at);

ALTER TABLE unl_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON unl_candidates FROM anon, authenticated;
REVOKE ALL ON SEQUENCE unl_candidates_id_seq FROM anon, authenticated;
