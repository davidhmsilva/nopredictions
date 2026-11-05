-- 087 — what a Create agent saw, and what it decided: the decision log.
--
-- Until now an agent left a trace only when it bought (`paper_trades`). The
-- fixtures it looked at and passed over left nothing: it could not say "I
-- skipped 14 games this week on the spread, and 9 of them won", and so nothing
-- could ever learn from them. This is step 1 of the agents that improve
-- themselves (an agent reviews its own record, proposes a variation, tests it
-- in shadow, and only then changes): first, keep what it saw.
--
-- One row per agent × fixture × leg it would back, upserted every cycle while
-- the fixture is in the entry window, so a fixture looked at nine times is one
-- row with `looks = 9` and the LAST state it was seen in. A row that entered
-- never changes again.
--
--   stage     how far the fixture got: 'rule' (a condition of the rule itself:
--             favourite, the leg exists, home/away confirmed), 'form' (a team's
--             recent games), 'book' (the price or the book), 'entered'.
--   reason    the condition that failed, in the runner's words; 'entered'.
--   leg_side  the plain rule's side (home, away, draw, over, under). A rule on
--             "the favourite at either end" is two legs on the same fixture.
--
-- What is NOT logged: fixtures outside the agent's universe (another league,
-- another market, a named team that is not playing). Those are not decisions.
--
-- Filled after kick-off by the settle job, for skipped and entered rows alike:
--   close_price  Polymarket's last 1-minute price at or before kick-off
--                (`pm_prices`, at most 5 min old, never 0 or 1; only within 7
--                days, past which the series can come back as the result).
--   won          the CLOB `winner` flag of the token the rule would have bought.
-- So a skip can be priced: what it would have cost, where the market closed,
-- and whether it won. That is the raw material a reviewer reads.
--
-- Written only by agent/lab_strategy_runner.py over DATABASE_URL. Nothing is
-- granted to the client.

begin;
set local lock_timeout = '5s';

create table if not exists public.lab_decisions (
    id              bigserial primary key,
    strategy_id     integer not null references public.strategies(id) on delete cascade,
    event_title     text not null,
    kickoff         timestamptz not null,
    leg_side        text not null,
    competition     text not null,
    home            text,                 -- the real home side, when ESPN confirmed it
    away            text,
    outcome         text,                 -- what it would buy, in words
    token_id        text,                 -- NULL when no leg could be named
    condition_id    text,
    stage           text not null check (stage in ('rule', 'form', 'book', 'entered')),
    reason          text not null,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    looks           integer not null default 1,
    minutes_to_ko   numeric,              -- at the last look
    gamma_mid       numeric,              -- the board's price at the last look
    bid             numeric,              -- the CLOB book, when it was read
    ask             numeric,
    ask_depth_usd   numeric,
    form            jsonb,                -- {"home": {...}, "away": {...}} as read
    paper_trade_id  integer references public.paper_trades(id) on delete set null,
    close_price     numeric,
    close_at        timestamptz,
    close_source    text,
    won             boolean,
    settled_at      timestamptz,
    unique (strategy_id, event_title, kickoff, leg_side)
);

create index if not exists lab_decisions_strategy
    on public.lab_decisions (strategy_id, kickoff desc);
-- The settle job's two queues: no close yet, no result yet.
create index if not exists lab_decisions_need_close
    on public.lab_decisions (kickoff)
    where close_source is null and token_id is not null;
create index if not exists lab_decisions_need_result
    on public.lab_decisions (kickoff)
    where won is null and token_id is not null;

comment on table public.lab_decisions is
  'What each Create agent looked at and decided, entered or skipped, with the price then and the close and result after. Step 1 of agents that review themselves.';

alter table public.lab_decisions enable row level security;
revoke all on table public.lab_decisions from public;
do $$ begin
    revoke all on table public.lab_decisions from anon, authenticated;
    revoke all on sequence public.lab_decisions_id_seq from anon, authenticated;
exception when undefined_object then null;
end $$;

commit;

-- What a reviewer reads, per agent:
--   select stage, reason, count(*), avg((won)::int) filter (where won is not null) as won,
--          avg(close_price / nullif(coalesce(ask, gamma_mid), 0) - 1) as close_vs_seen
--     from lab_decisions where strategy_id = :id group by 1, 2 order by 3 desc;
