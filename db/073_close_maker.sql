-- db/073 — Close Forecast (maker bids): the paper orders of agent/close_maker_agent.py.
--
-- H-STATS-CLOSE (#47). One row per resting bid the agent would have placed on
-- Polymarket: a 1X2 "Will <team> win?" Yes token whose T-24h mid sits at least
-- 2pp under where agent/close_model.py expects the price to CLOSE. A fill
-- creates a paper_trade (1u at the bid, no fee: makers pay none); an unfilled
-- bid expires at kick-off and STAYS here, because the filled-vs-unfilled gap
-- is the adverse-selection measurement (db/039-style rule_correct for makers):
-- a bid that only fills when the forecast is wrong shows up as filled rows
-- closing below the unfilled ones.
--
-- Every row gets the venue's close (last book read inside 15 min of kick-off)
-- and the payout, filled or not, so the counterfactual is exact per row.
-- RLS on, no policies: the agent and the site read over DATABASE_URL.

create table if not exists public.close_maker_orders (
    id              bigserial primary key,
    obs_version     int not null,
    strategy_id     int not null references public.strategies(id),
    created_at      timestamptz not null default now(),
    event_id        text not null,
    event_slug      text,
    title           text,
    league          text not null,
    kickoff         timestamptz not null,
    minutes_to_ko   numeric,
    home_team_id    int,
    away_team_id    int,
    home_name       text,
    away_name       text,
    side            text not null check (side in ('home', 'away')),
    team_name       text,
    condition_id    text not null,
    token_id        text not null,
    question        text,
    pm_bid          numeric,
    pm_ask          numeric,
    p_home          numeric,          -- the venue's three mids, normalised to 1
    p_draw          numeric,
    p_away          numeric,
    bid_depth_usd   numeric,
    ask_depth_usd   numeric,
    model_q         numeric not null, -- close_model's probability for this side
    gap             numeric not null, -- model_q − the side's normalised mid
    bid_price       numeric not null, -- the paper bid
    features        jsonb,
    status          text not null default 'resting'
                    check (status in ('resting', 'filled', 'expired', 'cancelled')),
    status_at       timestamptz not null default now(),
    filled_at       timestamptz,
    fill_rule       text,             -- 'ask_at_bid' | 'traded_through'
    touched_at      timestamptz,      -- a print AT our price: queue-dependent, not a fill
    close_bid       numeric,
    close_ask       numeric,
    close_mid       numeric,
    close_at        timestamptz,
    payout          numeric,          -- 1 / 0 / 0.5 once Polymarket resolves
    paper_trade_id  int references public.paper_trades(id),
    unique (strategy_id, token_id)
);

create index if not exists close_maker_orders_status_idx on public.close_maker_orders (status, kickoff);
create index if not exists close_maker_orders_strategy_idx on public.close_maker_orders (strategy_id, created_at desc);

alter table public.close_maker_orders enable row level security;
revoke all on public.close_maker_orders from anon, authenticated;
revoke all on sequence public.close_maker_orders_id_seq from anon, authenticated;
