-- db/071 — where each market OPENED on the venue, and its last quote before
-- kick-off. One row per market (Polymarket token / Kalshi leg), written by
-- agent/venue_open_recorder.py.
--
-- Why the venue and not Pinnacle: Pinnacle has no opening line we can read (the
-- Odds API gives the current snapshot; Football-Data's "Pinnacle (legacy)" is
-- the price on the day they collected, and it stopped carrying Pinnacle after
-- 2026-01-15). Polymarket's mid sits on Pinnacle's line near the close
-- (+0.10pp CI[−0.01,+0.20]), so the venue's own close is the benchmark we can
-- trade, and its OPEN is where the room is, if there is any.
--
-- Why recorded forward: PM's prices-history counts a missing side as 0 or 1
-- (ask/2, (bid+1)/2, 0.5000 on one-sided or empty books), and a book at listing
-- is exactly the empty kind, so the history has no usable opening. Stage M
-- starts at KO −90'. Polymarket lists football ~13 days ahead (median 318h,
-- measured 2026-10-10), so the first true openings land ~2 weeks after the
-- recorder starts; rows already listed when it started carry a `listed_at`
-- far before `first_seen_at` and are not openings.
--
-- RLS on, no policies: nothing here is for the client (db/045, db/048).

CREATE TABLE IF NOT EXISTS venue_market_quotes (
    venue           text        NOT NULL,        -- polymarket | kalshi
    market_key      text        NOT NULL,        -- PM token id / Kalshi ticker / board key
    sport           text        NOT NULL,        -- soccer | nfl | cfb | nba | nhl | mlb | wnba
    competition     text,
    event_key       text        NOT NULL,        -- PM event slug / Kalshi event ticker / ESPN id
    event_title     text,
    home            text,
    away            text,
    family          text        NOT NULL,        -- moneyline | draw | totals | spreads | btts
    line            numeric,
    outcome         text        NOT NULL,        -- home | away | draw | over | under | yes | <label>
    kickoff         timestamptz,
    kickoff_source  text,                        -- gamma | espn | kalshi_settle_est
    listed_at       timestamptz,                 -- the venue's own listing time, where it gives one

    first_seen_at   timestamptz NOT NULL,
    first_bid       numeric,
    first_ask       numeric,

    -- the OPEN: the first quote with both sides and spread <= 0.10
    open_at         timestamptz,
    open_bid        numeric,
    open_ask        numeric,
    open_depth_usd  numeric,
    open_source     text,                        -- gamma | clob | kalshi

    -- the last quote seen before kick-off, as of its last change
    last_at         timestamptz,
    last_bid        numeric,
    last_ask        numeric,
    last_depth_usd  numeric,
    last_source     text,

    depth_kind      text,                        -- ask_usd (top of book) | liquidity (Gamma liquidityNum)
    n_changes       integer     NOT NULL DEFAULT 0,
    PRIMARY KEY (venue, market_key)
);

CREATE INDEX IF NOT EXISTS venue_market_quotes_event_idx
    ON venue_market_quotes (sport, kickoff);

ALTER TABLE venue_market_quotes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON venue_market_quotes FROM anon, authenticated;
