-- 070_nba_agent.sql — the NBA every-game agent (paper).
--
-- The NBA twin of nfl_candidates (db/051): one row per priced candidate per
-- sharp snapshot — every Polymarket full-game moneyline / spread / total token
-- the agent could have bought, the fair value it read off the sharp line and
-- the net EV at the executable ask. The trade goes to paper_trades like every
-- other agent; this is what lets the choice be audited.
--
-- home_team / away_team / side hold the CANONICAL club (The Odds API's full
-- name, nba_agent.TEAMS), never Polymarket's nickname.
--
-- RLS on, no policies: nothing here is for the client (db/045, db/048).

CREATE TABLE IF NOT EXISTS nba_candidates (
    id              bigserial PRIMARY KEY,
    observed_at     timestamptz NOT NULL DEFAULT now(),
    obs_version     smallint    NOT NULL DEFAULT 1,
    game_slug       text        NOT NULL,
    kickoff         timestamptz NOT NULL,         -- tip-off
    minutes_to_ko   numeric,
    home_team       text,
    away_team       text,
    market_type     text        NOT NULL,         -- moneyline | spreads | totals
    line            numeric,                      -- the token's own line (team -x → -x)
    side            text        NOT NULL,         -- canonical club, 'Over' or 'Under'
    condition_id    text        NOT NULL,
    token_id        text        NOT NULL,
    pm_bid          numeric,
    pm_ask          numeric,
    pm_liquidity    numeric,
    fair            numeric,                      -- token value from the sharp line
    fair_source     text,                         -- sharp_exact | sharp_model | pm_mid
    fair_book       text,                         -- pinnacle | consensus(n)
    anchor_mu       numeric,                      -- expected margin, home team
    anchor_total    numeric,
    anchor_disagree numeric,                      -- model ML − book ML after the fit, pp
    fee_pp          numeric,
    ev_pct          numeric,                      -- net of fee and haircut, % of cash staked
    odds_snapshot_at timestamptz,
    chosen          boolean     NOT NULL DEFAULT false,
    paper_trade_id  integer REFERENCES paper_trades(id)
);

CREATE INDEX IF NOT EXISTS nba_candidates_game_idx ON nba_candidates (game_slug, observed_at);
CREATE INDEX IF NOT EXISTS nba_candidates_trade_idx ON nba_candidates (paper_trade_id)
    WHERE paper_trade_id IS NOT NULL;

ALTER TABLE nba_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON nba_candidates FROM anon, authenticated;
REVOKE ALL ON SEQUENCE nba_candidates_id_seq FROM anon, authenticated;
