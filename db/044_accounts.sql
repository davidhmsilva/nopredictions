-- 044 — accounts, metering, watchlist and alerts
--
-- The site becomes a product with a free tier and a paid one. What that needs
-- from the database is four tables and one rule about who may write to them.
--
-- 🔑 The metering table is the one that has to be right. A quota a client can
--    reset is not a quota. `usage_events` therefore carries NO policy at all:
--    with RLS on and nothing granted, the anon and authenticated roles cannot
--    read it, write it, or delete from it. Every read and write goes through
--    the server on DATABASE_URL, which bypasses RLS by design. The client is
--    never in a position to forge a count or erase one.
--
--    `watchlist` is the opposite case — it is the user's own list, it holds
--    nothing anyone could gain by forging, and a snappy UI wants to write it
--    directly. So it gets ordinary own-row policies.
--
--    `profiles` gets SELECT for its owner and nothing else. A client that
--    could UPDATE its own row could set `plan = 'pro'`, and column-level
--    grants are a weaker guarantee than simply not having the policy: plan
--    changes arrive from the Stripe webhook, on the server, or not at all.

begin;

-- ── profiles ────────────────────────────────────────────────────────────────

create table if not exists public.profiles (
  id                     uuid primary key references auth.users(id) on delete cascade,
  email                  text,
  created_at             timestamptz not null default now(),

  -- 'free' | 'pro'. Written by the Stripe webhook and by nothing else.
  plan                   text        not null default 'free'
                                     check (plan in ('free', 'pro')),
  -- Stripe's own subscription status, kept verbatim so a support question can
  -- be answered without guessing at our own translation of it:
  -- trialing | active | past_due | canceled | incomplete | incomplete_expired | unpaid
  plan_status            text,
  stripe_customer_id     text unique,
  stripe_subscription_id text unique,
  -- When the paid period ends. A cancelled subscription keeps `plan='pro'`
  -- until this passes — someone who paid for the month gets the month.
  current_period_end     timestamptz
);

comment on table public.profiles is
  'One row per authenticated user. `plan` is written by the Stripe webhook only; there is deliberately no UPDATE policy for the client.';

alter table public.profiles enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select using (auth.uid() = id);

-- A profile must exist the moment the user does, or the first request after
-- sign-up races the row into existence and reads a plan that is not there.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ── metering ────────────────────────────────────────────────────────────────

create table if not exists public.usage_events (
  id         bigserial   primary key,
  user_id    uuid        not null references auth.users(id) on delete cascade,
  -- 'lab' | 'wallet'. Named rather than free text so a typo in a route handler
  -- cannot silently create a third, unmetered feature.
  feature    text        not null check (feature in ('lab', 'wallet')),
  created_at timestamptz not null default now()
);

comment on table public.usage_events is
  'One row per metered action. RLS is on with NO policies: only the server, on DATABASE_URL, may read or write. A quota the client can delete from is not a quota.';

create index if not exists usage_events_user_feature_time
  on public.usage_events (user_id, feature, created_at desc);

alter table public.usage_events enable row level security;

-- ── watchlist ───────────────────────────────────────────────────────────────

create table if not exists public.watchlist (
  user_id    uuid        not null references auth.users(id) on delete cascade,
  -- The Polymarket event slug, the same identity /game/<slug> takes.
  slug       text        not null,
  home       text,
  away       text,
  created_at timestamptz not null default now(),
  primary key (user_id, slug)
);

comment on table public.watchlist is
  'Fixtures a user is following. Replaces the np_watchlist localStorage key, which was per-browser and invisible to alerts.';

alter table public.watchlist enable row level security;

drop policy if exists watchlist_all_own on public.watchlist;
create policy watchlist_all_own on public.watchlist
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── alerts ──────────────────────────────────────────────────────────────────

create table if not exists public.alerts (
  id         bigserial   primary key,
  user_id    uuid        not null references auth.users(id) on delete cascade,
  slug       text        not null,
  -- What the user asked to be told about.
  --   kickoff    — the fixture goes live
  --   book_open  — a book on it grades CLEAN for the first time
  kind       text        not null check (kind in ('kickoff', 'book_open')),
  created_at timestamptz not null default now(),
  -- Set when the alert has been sent, so it fires once.
  fired_at   timestamptz,
  unique (user_id, slug, kind)
);

comment on table public.alerts is
  'Pro-tier alert subscriptions. `fired_at` makes delivery idempotent — the sender claims a row by stamping it, so a restart cannot double-send.';

create index if not exists alerts_pending on public.alerts (slug) where fired_at is null;

alter table public.alerts enable row level security;

drop policy if exists alerts_all_own on public.alerts;
create policy alerts_all_own on public.alerts
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

commit;
