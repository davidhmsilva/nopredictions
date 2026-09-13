-- 048 — the client keys read; they write only where a policy says they may
--
-- Found 2026-09-11 while designing private, per-user strategies. `anon` and
-- `authenticated` — the two roles the browser holds, whose key ships inside
-- the site's JavaScript — carried INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER
-- and REFERENCES on ALL 50 relations in `public`. Supabase's default
-- privileges grant everything on every table `postgres` creates, and db/045
-- turned RLS on without taking the grants back.
--
-- RLS covered most of it. It did not cover:
--
--   * VIEWS. v_pressure_trades, v_ht_pressure_trades, v_fav_ht_trades and
--     v_research_log are auto-updatable and run with their OWNER's rights, so
--     an UPDATE or DELETE sent through them over PostgREST skips the base
--     table's RLS entirely. `won` on v_pressure_trades IS
--     pressure_observations.goal_before_ft: anyone holding the public key
--     could have rewritten the public record's results, or deleted entries.
--   * TRUNCATE, which row security never applies to. PostgREST exposes no
--     TRUNCATE, so it was not reachable today — one exposed function away.
--
-- 🔑 The rule after this migration: the client reads what RLS lets it read,
--    and writes exactly three things, each behind an own-row or insert-only
--    policy. Every other write goes through the server on DATABASE_URL, which
--    is how the site and every agent already work.
--
-- 🔑 And NEW tables start closed. Without the default-privilege change, the
--    next table created here would arrive with TRUNCATE for anon again — and
--    with SELECT, which on a table someone forgot to put RLS on is a public
--    read. A table the site must read now needs an explicit GRANT SELECT.

begin;
set local lock_timeout = '5s';

do $$
declare r record;
begin
  for r in
    select c.relname
      from pg_class c
     where c.relnamespace = 'public'::regnamespace
       and c.relkind in ('r', 'v', 'm', 'p')
  loop
    execute format(
      'revoke insert, update, delete, truncate, references, trigger on public.%I from anon, authenticated',
      r.relname);
  end loop;
end $$;

-- The three places a client legitimately writes, each behind its own policy
-- (db/044 watchlist_all_own + alerts_all_own, db/027 waitlist_insert_anon).
grant insert, update, delete on public.watchlist to authenticated;
grant insert, update, delete on public.alerts    to authenticated;
grant insert                 on public.waitlist_signups to anon;

alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;

commit;
