-- 047 — pay first, make the account afterwards
--
-- The funnel was: create an account, then upgrade. Two steps, and the first one
-- asks for commitment before the product has been paid for. SteamWatch does the
-- opposite — email straight into Stripe checkout — and it is the better order.
--
-- 🔑 The problem this table solves: a payment arrives for an email address that
--    has no auth user yet. `profiles.id` references `auth.users(id)`, so there
--    is nowhere to write it. Creating the user from the webhook would need the
--    Supabase service-role key, which this deployment does not hold.
--
--    So the grant is stored against the EMAIL, and claimed by the account when
--    it is created. The webhook always writes here; `handle_new_user` reads it.
--    That makes the two events independent and order-free: pay then sign up,
--    or sign up then pay, and both end in the same place.
--
-- ⚠️ Email is the join key, which is only safe because Supabase is the one
--    issuing it — the grant is claimed by whoever proves control of that
--    address through auth, never by whoever types it. A grant that is never
--    claimed simply sits here, which is also the record of a payment whose
--    owner never came back.

begin;

create table if not exists public.pro_grants (
  email                  text        primary key,
  stripe_customer_id     text,
  stripe_subscription_id text,
  plan_status            text,
  current_period_end     timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  -- The account that took it up, once one exists.
  claimed_by             uuid        references auth.users(id) on delete set null,
  claimed_at             timestamptz
);

comment on table public.pro_grants is
  'A paid subscription held against an email that may not have an account yet. Written only by the Stripe webhook; claimed by handle_new_user() when the account is created. RLS on with no policies — the server is the only reader.';

-- Only the server, over DATABASE_URL. Same reasoning as usage_events (db/044):
-- a client that could read this could learn who is paying, and one that could
-- write it could grant itself Pro.
alter table public.pro_grants enable row level security;

-- ── the claim ───────────────────────────────────────────────────────────────
--
-- Replaces db/044's version. Same job — a profile must exist the moment the
-- user does — plus: if a payment is already waiting for this address, the new
-- profile starts on Pro rather than on free.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  g public.pro_grants%rowtype;
begin
  select * into g from public.pro_grants
   where email = lower(new.email)
     and claimed_by is null;

  insert into public.profiles (
    id, email, plan, plan_status,
    stripe_customer_id, stripe_subscription_id, current_period_end
  )
  values (
    new.id,
    new.email,
    -- Only an actually-paid grant grants. A cancelled or unpaid one is
    -- recorded and does nothing, which is the same rule the webhook applies.
    case when g.plan_status in ('active', 'trialing') then 'pro' else 'free' end,
    g.plan_status,
    g.stripe_customer_id,
    g.stripe_subscription_id,
    g.current_period_end
  )
  on conflict (id) do nothing;

  if g.email is not null then
    update public.pro_grants
       set claimed_by = new.id, claimed_at = now()
     where email = g.email;
  end if;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

commit;
