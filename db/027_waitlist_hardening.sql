-- 027 — waitlist hardening: real email validation, disposable-domain blocking,
-- per-IP rate limiting.
--
-- Why this lives in the database and not in the form: the `anon` role can INSERT
-- into waitlist_signups directly through PostgREST, so anything enforced only in
-- the browser is decoration. A bot never loads the page.
--
-- The old constraint was `position('@' in email) > 1`, which accepts 'a@b' and a
-- 10,000-character string.

-- ── 1. Store a hash of the caller IP, never the IP itself ───────────────────
alter table public.waitlist_signups
  add column if not exists ip_hash text;

comment on column public.waitlist_signups.ip_hash is
  'md5 of the client IP — used only to rate-limit signups. Not reversible to an IP in practice, and never displayed.';

-- ── 2. Disposable / throwaway domains ───────────────────────────────────────
create table if not exists public.waitlist_blocked_domains (
  domain text primary key,
  note   text,
  added_at timestamptz not null default now()
);

-- RLS on with no policy at all = nobody but the service role can touch it.
alter table public.waitlist_blocked_domains enable row level security;

insert into public.waitlist_blocked_domains (domain, note) values
  ('mailinator.com',    'disposable'),
  ('guerrillamail.com', 'disposable'),
  ('10minutemail.com',  'disposable'),
  ('tempmail.com',      'disposable'),
  ('temp-mail.org',     'disposable'),
  ('throwawaymail.com', 'disposable'),
  ('yopmail.com',       'disposable'),
  ('sharklasers.com',   'disposable'),
  ('trashmail.com',     'disposable'),
  ('getnada.com',       'disposable'),
  ('dispostable.com',   'disposable'),
  ('fakeinbox.com',     'disposable'),
  ('maildrop.cc',       'disposable'),
  ('mohmal.com',        'disposable'),
  ('spam4.me',          'disposable')
on conflict (domain) do nothing;

-- ── 3. Shape of the address ─────────────────────────────────────────────────
-- Deliberately not RFC-complete: RFC 5322 allows addresses no mail provider
-- issues. This accepts what real inboxes look like and rejects the rest.
alter table public.waitlist_signups
  drop constraint if exists waitlist_signups_email_check;

alter table public.waitlist_signups
  drop constraint if exists waitlist_email_shape;

alter table public.waitlist_signups
  add constraint waitlist_email_shape check (
    length(email) between 6 and 254
    and email ~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$'
    and email !~ '\.\.'
    and email = lower(email)
  );

-- ── 4. The guard trigger ────────────────────────────────────────────────────
-- SECURITY DEFINER because it has to read waitlist_blocked_domains and count
-- existing signups — both closed to `anon` by RLS.
create or replace function public.waitlist_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ip     text := '';
  v_domain text;
  v_recent integer;
begin
  -- Normalise first so the unique index on lower(email) and the shape
  -- constraint both see the same thing the rest of the app will see.
  new.email := lower(btrim(new.email));
  new.source := coalesce(nullif(btrim(new.source), ''), 'landing');
  v_domain := split_part(new.email, '@', 2);

  if exists (
    select 1 from public.waitlist_blocked_domains b where b.domain = v_domain
  ) then
    raise exception 'waitlist_disposable_domain' using errcode = 'P0001';
  end if;

  -- PostgREST publishes the request headers; a direct SQL insert has none, so
  -- a missing header must never block the write.
  begin
    v_ip := split_part(
      coalesce(current_setting('request.headers', true)::json ->> 'x-forwarded-for', ''),
      ',', 1
    );
  exception when others then
    v_ip := '';
  end;

  if btrim(v_ip) <> '' then
    new.ip_hash := md5(btrim(v_ip));

    select count(*) into v_recent
      from public.waitlist_signups s
     where s.ip_hash = new.ip_hash
       and s.created_at > now() - interval '1 hour';

    -- Three a day per household is generous; a bot wants thousands.
    if v_recent >= 3 then
      raise exception 'waitlist_rate_limited' using errcode = 'P0001';
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists waitlist_guard_trg on public.waitlist_signups;

create trigger waitlist_guard_trg
  before insert on public.waitlist_signups
  for each row execute function public.waitlist_guard();

-- ── 5. Rate-limit lookups hit ip_hash + created_at on every insert ──────────
create index if not exists waitlist_signups_ip_recent_idx
  on public.waitlist_signups (ip_hash, created_at desc)
  where ip_hash is not null;
