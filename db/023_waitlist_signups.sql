-- 023: waitlist signups from the /landing page form
-- Applied to Supabase on 2026-07-07 (migration name: waitlist_signups)

create table if not exists waitlist_signups (
  id           bigint generated always as identity primary key,
  email        text not null check (position('@' in email) > 1),
  main_sport   text,
  excites_most text,
  source       text not null default 'landing',
  created_at   timestamptz not null default now()
);

create unique index if not exists waitlist_signups_email_uniq
  on waitlist_signups (lower(email));

alter table waitlist_signups enable row level security;

-- public form: anon can insert, nobody can read via the anon key
create policy waitlist_insert_anon on waitlist_signups
  for insert to anon with check (true);
