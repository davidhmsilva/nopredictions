-- 049 — strategies get an owner, and a user can make their own
--
-- Until now a "strategy" was a Python daemon somebody wrote by hand, and the
-- public site showed all of them. The product changes shape (2026-09-11):
--
--   * every strategy belongs to a user — the hand-written pressure arms belong
--     to the site's owner, and are HIS, not a public exhibit;
--   * a user builds a strategy from a Lab theory, keeps it with the backtest
--     that justified it, and can switch it on to paper-trade today's
--     Polymarket boards (agent/lab_strategy_runner.py);
--   * a user may publish a strategy to the public leaderboard. Nothing is
--     public unless its owner says so.
--
-- Additive only. The migration that takes the public read AWAY (db/050) is
-- separate and is applied after the site that reads through the server is
-- deployed — the other order breaks the page in production.

begin;
set local lock_timeout = '5s';

-- 'owner' is the site's operator: unlimited Lab/Wallet, and the only account
-- the hand-written agents can belong to. Written by hand in SQL, never by the
-- client — profiles has no UPDATE policy and, since db/048, no UPDATE grant.
alter table public.profiles
  add column if not exists role text not null default 'user'
    check (role in ('user', 'owner'));

alter table public.strategies
  -- NULL = nobody's yet (legacy arms), never "everybody's".
  add column if not exists owner_id       uuid references auth.users(id) on delete set null,
  -- 'agent' = a hand-written daemon; 'lab' = built from a Lab theory and run
  -- by lab_strategy_runner.py. The runner only ever touches 'lab'.
  add column if not exists source         text not null default 'agent'
    check (source in ('agent', 'lab')),
  add column if not exists theory         text,          -- the user's words
  add column if not exists interpretation text,          -- what was actually tested
  add column if not exists spec           jsonb,         -- the Lab Spec, verbatim
  -- Recomputed by the SERVER at save time from `spec`, never taken from the
  -- client: a public leaderboard cannot rank on numbers a browser supplied.
  add column if not exists backtest       jsonb,
  add column if not exists run_status     text not null default 'draft'
    check (run_status in ('draft', 'running', 'paused')),
  -- Why the runner refuses a spec it cannot evaluate live, shown to the user.
  add column if not exists run_blocker    text,
  add column if not exists is_public      boolean not null default false,
  add column if not exists created_at     timestamptz not null default now();

create index if not exists strategies_owner on public.strategies (owner_id);
create index if not exists strategies_running
  on public.strategies (run_status) where source = 'lab';

-- The hand-written arms that still run say so; everything else is paused.
update public.strategies
   set run_status = case when id in (16, 17, 18, 19) and retired_at is null
                         then 'running' else 'paused' end
 where source = 'agent';

commit;
