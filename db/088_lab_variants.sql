-- 088 — shadow variants: a change to an agent's rule, run beside it, never as it.
--
-- Step 2 of agents that review their own record. A variant is the agent's spec
-- with one change (a wider odds band, another league, entering three hours
-- before kick-off instead of 45 minutes). The runner holds it against the same
-- fixtures, in the same cycle, at the same CLOB prices as the agent itself, and
-- logs what it would have done in `lab_decisions` with its `variant_id`:
--
--   * a variant never writes `paper_trades`. Its "entries" are decision rows
--     with stage 'entered' and no paper_trade_id, so nothing it does can reach
--     the agent's record, the agent's page or the Marketplace;
--   * the settle job already prices every decision row (Polymarket's close and
--     the CLOB winner), so a variant gets a CLV and a result with no new code
--     there;
--   * the agent and its variants are compared on the SAME fixtures from the
--     moment the variant started, which is the only fair comparison.
--
-- A variant that wins is not swapped into the agent: a rule is never edited in
-- place, because the record was earned on it. Promoting one makes a new agent
-- (status 'promoted' here records that it happened).
--
--   spec     the full spec the variant runs (the agent's, with `change` applied)
--   change   only what differs, as {key: new value}, null meaning "removed"
--   why      the hypothesis, in words: what it should do better and why
--   created_by  'operator' by hand, 'review' when the weekly review proposed it
--
-- ⚠️ TWO PHASES. lab_decisions' key gains variant_id. The old key cannot be
-- dropped before the runner that writes the new one is on the server, or the
-- running code's ON CONFLICT finds no constraint and loses its log (the trades
-- are never at risk: the log is written after them, in its own try). So:
--   phase 1 (this file, up to the marker): before the server pull;
--   phase 2 (after the marker): after the server pull.
-- With both keys present, a variant row on a fixture the agent already logged
-- is refused by the old key; the runner logs that and carries on.

-- ── phase 1 ─────────────────────────────────────────────────────────────────
begin;
set local lock_timeout = '5s';

create table if not exists public.lab_variants (
    id          serial primary key,
    strategy_id integer not null references public.strategies(id) on delete cascade,
    spec        jsonb not null,
    change      jsonb not null,
    why         text not null check (char_length(why) between 3 and 1000),
    created_by  text not null default 'operator' check (created_by in ('operator', 'review')),
    status      text not null default 'running'
                check (status in ('running', 'stopped', 'blocked', 'promoted')),
    blocker     text,                       -- why the runner refused it (status 'blocked')
    created_at  timestamptz not null default now(),
    stopped_at  timestamptz
);
create index if not exists lab_variants_strategy on public.lab_variants (strategy_id, status);

comment on table public.lab_variants is
  'A change to a Create agent''s rule, run in shadow beside it on the same fixtures. Never trades, never reaches the agent''s record. Step 2 of agents that review themselves.';

alter table public.lab_variants enable row level security;
revoke all on table public.lab_variants from public;
do $$ begin
    revoke all on table public.lab_variants from anon, authenticated;
    revoke all on sequence public.lab_variants_id_seq from anon, authenticated;
exception when undefined_object then null;
end $$;

alter table public.lab_decisions
    add column if not exists variant_id integer references public.lab_variants(id) on delete cascade;

-- The new key: the agent's own rows have variant_id NULL, and NULLS NOT
-- DISTINCT keeps them one per fixture exactly as before.
create unique index if not exists lab_decisions_key
    on public.lab_decisions (strategy_id, variant_id, event_title, kickoff, leg_side)
    nulls not distinct;
create index if not exists lab_decisions_variant
    on public.lab_decisions (variant_id, kickoff desc) where variant_id is not null;

commit;

-- ── phase 2: run only once the new runner is on the server ─────────────────
-- alter table public.lab_decisions
--     drop constraint if exists lab_decisions_strategy_id_event_title_kickoff_leg_side_key;
