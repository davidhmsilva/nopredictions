-- 082 — the agent marketplace: publish an agent, copy someone else's
--
-- The product changed shape on 2026-10-28, by the operator's direction: the
-- app is about agents and nothing else. A user describes an idea, Create turns
-- it into a strategy and deploys it as an agent, the user manages their agents,
-- and an agent its owner publishes goes on the Marketplace, where anyone can
-- copy it into their own account and run it.
--
-- `is_public` (db/049) already says whether an agent is published. What was
-- missing:
--   * copied_from   the agent a copy was made from. A copy is a snapshot: its
--                   own row, its own owner, its own paper trades from the
--                   moment it runs. The original's record is never shared.
--   * published_at  when it was FIRST published. Unpublishing keeps it, so a
--                   republish does not jump the queue as new.
--   * public_name   the name an author is shown under — never the email. NULL
--                   is a pseudonym the server derives (site/app/lib/agents.ts).
--
-- Additive only, and nothing here is granted to the client: every read and
-- write goes over DATABASE_URL in site/app/lib/agents.ts (db/048, db/050).

begin;
set local lock_timeout = '5s';

alter table public.strategies
  add column if not exists copied_from  integer references public.strategies(id) on delete set null,
  add column if not exists published_at timestamptz;

alter table public.profiles
  add column if not exists public_name text
    check (public_name is null or char_length(public_name) between 2 and 32);

-- The marketplace lists published agents; every card counts its copies.
create index if not exists strategies_published
  on public.strategies (published_at desc)
  where is_public and retired_at is null;
create index if not exists strategies_copied_from
  on public.strategies (copied_from)
  where copied_from is not null;

commit;
