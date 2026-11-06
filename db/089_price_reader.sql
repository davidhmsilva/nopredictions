-- 089 — Price Reader: every sport, every market, decided by the price alone (paper)
--
-- The operator's brief (2026-11-06): an agent that tries to call a result in any
-- Polymarket sports market, in any sport, by looking only at the price; free to
-- buy before or during the event, to hold or to sell; that reviews its own
-- trades every day and changes how it trades to do better.
--
-- Three tables, all server-only (RLS on, nothing granted to anon/authenticated):
--
--   price_reader_arms       the ways of reading a price it tries ("arms"), each a
--                           fixed rule. mode = active (books paper trades),
--                           shadow (priced and settled, never booked) or
--                           stopped. The review moves arms between modes; it
--                           never edits an arm's params: a change is a new arm.
--   price_reader_positions  every position, booked or shadow. An arm's record is
--                           ALL of its positions; `booked` only says whether it
--                           also wrote a paper trade (the agent's tab).
--   price_reader_reviews    one row a day: what the review read, what it changed
--                           and why, in words, for the agent's page.
--
-- Arms are seeded by price_reader_agent.py (ensure_arms), from the code, so the
-- params live in one place.

begin;
set local lock_timeout = '5s';

create table if not exists public.price_reader_arms (
    id               serial primary key,
    key              text        not null unique,
    base_key         text        not null,
    label            text        not null,
    params           jsonb       not null,
    mode             text        not null check (mode in ('active', 'shadow', 'stopped')),
    mode_since       timestamptz not null default now(),
    daily_cap        integer     not null default 25 check (daily_cap between 0 and 200),
    excluded_sports  text[]      not null default '{}',
    note             text,
    created_at       timestamptz not null default now()
);

create table if not exists public.price_reader_positions (
    id                  bigserial primary key,
    arm_id              integer     not null references public.price_reader_arms(id),
    arm_key             text        not null,
    booked              boolean     not null,
    paper_trade_id      integer     references public.paper_trades(id),
    -- the event
    event_id            text        not null,
    event_slug          text,
    event_title         text,
    event_start         timestamptz not null,
    sport               text        not null,
    league              text,
    phase               text        not null check (phase in ('pre', 'live', 'post')),
    minutes_from_start  numeric(8,1),
    -- the market and the token
    condition_id        text        not null,
    token_id            text        not null,
    market_type         text,
    family              text,
    question            text,
    outcome             text,
    label               text,
    -- the entry
    gamma_ask           numeric(6,4),
    entry_ask           numeric(6,4) not null,
    entry_bid           numeric(6,4),
    ask_depth_usd       numeric(14,2),
    move_pp             numeric(7,2),
    shares              numeric(14,6) not null,
    fee_rate            numeric(6,4) not null,
    stake               numeric(10,4) not null,
    exit_rule           text        not null check (exit_rule in ('hold', 'flip')),
    exit_due_at         timestamptz,
    entered_at          timestamptz not null default now(),
    -- the price at the start, for a position bought before it (CLV on Polymarket)
    close_price         numeric(6,4),
    clv                 numeric(8,5),
    -- the exit
    status              text        not null default 'open'
                        check (status in ('open', 'sold', 'settled', 'void')),
    exit_at             timestamptz,
    exit_bid            numeric(6,4),
    exit_reason         text,
    payout              numeric(14,6),
    pl                  numeric(14,6),
    unique (arm_id, event_id)
);

create index if not exists price_reader_positions_open_idx
    on public.price_reader_positions (status, event_start) where status = 'open';
create index if not exists price_reader_positions_arm_idx
    on public.price_reader_positions (arm_id, entered_at);
create index if not exists price_reader_positions_ptid_idx
    on public.price_reader_positions (paper_trade_id) where paper_trade_id is not null;

create table if not exists public.price_reader_reviews (
    id            serial primary key,
    review_date   date        not null unique,
    created_at    timestamptz not null default now(),
    summary       text        not null,
    arms          jsonb       not null,
    changes       jsonb       not null
);

alter table public.price_reader_arms      enable row level security;
alter table public.price_reader_positions enable row level security;
alter table public.price_reader_reviews   enable row level security;
revoke all on public.price_reader_arms, public.price_reader_positions, public.price_reader_reviews
  from public;
do $$ begin
  revoke all on public.price_reader_arms, public.price_reader_positions, public.price_reader_reviews
    from anon, authenticated;
exception when undefined_object then null;
end $$;

-- The hypothesis and the agent, owned by the operator so it shows on their tab.
insert into public.research_hypotheses (title, description, rationale, source, status, created_by)
select 'H-PRICE-READER — a price alone, read the right way, calls sports results better than its odds',
       'Seven pre-registered ways of reading a Polymarket price (favourite before the start, follow the pre-match move, fade one exact score, back the late leader, ride or fade a live move, collect after the whistle), each with two shadow variants, across every sport and market. 1u paper at the CLOB ask plus the taker fee. A daily review stops an arm whose 90% interval is wholly below zero (50+ bets), brings it back when its shadow record clears zero, and promotes a variant only when it beats its base arm on the same days by 1.645 standard errors.',
       'The operator''s brief (2026-11-06). Priors against most arms: PM in-play football is calibrated within its costs (#44), PM''s pre-match mid is the Pinnacle line, dropping-odds followers lost as taker and maker. Priors for: swisstony/0x2c33 earn ~1% of volume buying 0.90+ near-certainties and exact-score Nos, GSX- made half its profit in the 20 minutes after the whistle. Nothing has tested these outside football; the review is there to find which, if any, pays.',
       'user', 'pre_registered', 'claude'
where not exists (select 1 from public.research_hypotheses where title like 'H-PRICE-READER%');

insert into public.strategies (hypothesis_id, name, source, owner_id, theory, interpretation, rules,
                               run_status, promoted_at)
select h.id, 'Price Reader — every sport, price only', 'agent',
       (select id from public.profiles where role = 'owner' limit 1),
       'A sports price, read the right way, says more than the odds it shows. Which way, if any, is what this agent is finding out.',
       'Watches every game market of every sport on Polymarket, about 12,000 at a time, and decides from the price alone: its level, how it moved, its book and the clock. Never the score. Seven ways of reading it run side by side; every day it reviews its own bets, stops what loses and promotes what wins on evidence. 1u at the Polymarket ask plus the fee, paper.',
       '{"kind": "price_reader", "phase": "paper-only", "venue": "polymarket", "stake_u": 1, "self_settling": true}'::jsonb,
       'running', now()
  from public.research_hypotheses h
 where h.title like 'H-PRICE-READER%'
   and not exists (select 1 from public.strategies where name = 'Price Reader — every sport, price only');

commit;
