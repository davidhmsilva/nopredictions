-- db/080 — NBA Every Game, maker bids: the paper orders of agent/nba_maker.py.
--
-- H-NBA-MAKER. Taking the ask on the 36 FORCED NBA bets of 2026-10-20 → 10-25
-- cost -4.2%; the same tokens at the bid, no fee, would have been -1.9%, but
-- only 8 of 36 bids placed 36 min before tip-off ever traded at the bid and 2
-- traded through it. So this arm bids EARLY (from 24h out) on the two markets
-- that trade: the moneyline and the main spread, BOTH sides of each, at a
-- fixed price. No fair value is needed to choose a side.
--
-- One row per resting bid. A fill (a trade strictly BELOW the bid, read as the
-- Yes-equivalent price across both tokens) creates a paper_trade (1u at the
-- bid, no fee: makers pay none). A print AT the bid, or the ask coming down to
-- it, depends on queue position and is only `touched_at`. An unfilled bid
-- expires at tip-off and STAYS here: the filled-vs-expired gap on close and
-- payout is the adverse-selection measurement (db/073's design).
--
-- Every row gets the close (the CLOB's prices-history at the last point at or
-- before tip-off) and the payout, filled or not.
-- RLS on, no policies: the agent reads and writes over DATABASE_URL.

create table if not exists public.nba_maker_orders (
    id                    bigserial primary key,
    obs_version           int not null,
    strategy_id           int not null references public.strategies(id),
    created_at            timestamptz not null default now(),
    game_slug             text not null,
    title                 text,
    kickoff               timestamptz not null,           -- tip-off
    minutes_to_ko         numeric,                        -- when the bid was posted
    home_team             text,                           -- canonical club (nba_agent.TEAMS)
    away_team             text,
    market_type           text not null check (market_type in ('moneyline', 'spreads')),
    line                  numeric,                        -- the bought side's own line (team -x → -x)
    side                  text not null,                  -- canonical club
    condition_id          text not null,
    token_id              text not null,
    question              text,
    tick                  numeric not null,
    pm_bid                numeric,                        -- the token's book when the bid was posted
    pm_ask                numeric,
    bid_depth_usd         numeric,                        -- top five levels
    ask_depth_usd         numeric,
    liquidity_usd         numeric,                        -- Gamma's, the whole market
    bid_price             numeric not null,               -- the paper bid
    fair                  numeric,                        -- sharp fair value if a snapshot matched, for analysis only
    fair_source           text,                           -- sharp_exact | sharp_model | NULL
    odds_snapshot_at      timestamptz,
    status                text not null default 'resting'
                          check (status in ('resting', 'filled', 'expired')),
    status_at             timestamptz not null default now(),
    filled_at             timestamptz,
    fill_price            numeric,                        -- the Yes-equivalent print that traded through
    touched_at            timestamptz,                    -- first print AT the bid, or the ask at it
    trades_checked_until  timestamptz,                    -- trades are read from here on, never twice
    close_price           numeric,                        -- prices-history, last point <= tip-off
    close_at              timestamptz,
    payout                numeric,                        -- 1 / 0 / 0.5 once Polymarket resolves
    paper_trade_id        int references public.paper_trades(id),
    unique (strategy_id, token_id)
);

create index if not exists nba_maker_orders_status_idx on public.nba_maker_orders (status, kickoff);
create index if not exists nba_maker_orders_game_idx on public.nba_maker_orders (strategy_id, game_slug);

alter table public.nba_maker_orders enable row level security;
revoke all on public.nba_maker_orders from anon, authenticated;
revoke all on sequence public.nba_maker_orders_id_seq from anon, authenticated;
