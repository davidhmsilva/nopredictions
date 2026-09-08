-- 046 — newsletter subscribers
--
-- The site had a newsletter form once. It set local state, showed a tick, and
-- sent nowhere; it was deleted on 2026-09-06 along with the landing page. This
-- is the same feature built the other way round: the address is stored for
-- real, and the page says plainly that nothing is being sent yet.
--
-- 🔑 **RLS on, NO policies.** db/027 hardened the old waitlist with a trigger,
--    because `anon` could INSERT straight into it through PostgREST and
--    anything enforced in the browser was decoration. That was the right fix
--    for a site with no server. This site has one now, so the stronger answer
--    is available: `anon` cannot write this table at all, and /api/newsletter
--    writes it over DATABASE_URL. Validation and the rate limit live in code
--    that can be tested, with the constraints below as the backstop that holds
--    even if a future caller forgets.
--
-- The blocked-domain list is db/027's, reused rather than duplicated — one
-- list, one place to add to.

begin;

create table if not exists public.newsletter_subscribers (
  id           bigserial   primary key,
  email        text        not null,
  -- Where they signed up from: 'insights', 'article:<slug>', 'footer'. Worth
  -- keeping — the first thing anyone asks of a list is which page built it.
  source       text        not null default 'insights',
  created_at   timestamptz not null default now(),
  -- md5 of the client IP, for rate limiting only. Not reversible in practice,
  -- never displayed. Same convention as waitlist_signups.ip_hash (db/027).
  ip_hash      text,
  -- Set when they ask to stop. The row is kept: deleting it would let the same
  -- address be re-added by anyone who guesses it, and would lose the fact that
  -- they said no.
  unsubscribed_at timestamptz,
  -- Filled when confirmation email actually exists. Until then every row is
  -- unconfirmed, and that is the honest state.
  confirmed_at timestamptz
);

comment on table public.newsletter_subscribers is
  'Newsletter list. RLS on with no policies: only the server, over DATABASE_URL, may read or write. Nothing sends yet — confirmed_at stays null until an email provider exists.';

-- Deliberately not RFC-complete: RFC 5322 allows addresses no provider issues.
-- This accepts what real inboxes look like and rejects the rest. Copied from
-- db/027 so the two tables cannot disagree about what an email is.
alter table public.newsletter_subscribers
  drop constraint if exists newsletter_email_shape;

alter table public.newsletter_subscribers
  add constraint newsletter_email_shape check (
    length(email) between 6 and 254
    and email ~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$'
    and email !~ '\.\.'
    and email = lower(email)
  );

-- One row per address. A second signup is an update of `source`, never a
-- duplicate — a list with the same person twice mails them twice.
create unique index if not exists newsletter_subscribers_email_key
  on public.newsletter_subscribers (email);

-- The rate-limit lookup, on every insert.
create index if not exists newsletter_subscribers_ip_recent_idx
  on public.newsletter_subscribers (ip_hash, created_at desc)
  where ip_hash is not null;

alter table public.newsletter_subscribers enable row level security;

commit;
