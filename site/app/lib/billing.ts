/** Who is paying, decided from Stripe's CURRENT state — never from an event.
 *
 *  🔑 The webhook used to write the subscription carried inside each event.
 *     Stripe does not promise order, so a stale `customer.subscription.updated`
 *     (still `active`) delivered after the `deleted` put a cancelled customer
 *     back on Pro. Now every event is only a nudge: "something changed for this
 *     customer". The handler asks Stripe what is true now and writes that, so
 *     replaying, duplicating or reordering events all converge on the same row.
 *
 *  🔑 The plan is decided per CUSTOMER, not per subscription. Someone who
 *     subscribed monthly and then yearly has two subscriptions; cancelling
 *     either one used to write `plan='free'` while the other was still being
 *     charged. Pro is now "any subscription on this customer is active or
 *     trialing". Checkout refuses to open a second one (LIVE below), but the
 *     sync has to be right even for the ones created by hand in the dashboard.
 *
 *  Server only: imports the Stripe secret and the database.
 */

import type Stripe from 'stripe'
import { getSql } from './db'
import { stripe } from './stripe'

/** Statuses that entitle someone to the product. `past_due` deliberately does
 *  not: Stripe is still retrying the card, and access resumes the moment it
 *  succeeds and sends another event. */
export const PAID: ReadonlySet<string> = new Set(['active', 'trialing'])

/** Statuses of a subscription that still exists and still bills, or is still
 *  trying to. A second checkout on top of any of these is a second charge.
 *  `incomplete` is left out on purpose: that is an abandoned first payment,
 *  and blocking on it would lock out the person who closed the tab. */
export const LIVE: ReadonlySet<string> = new Set([
  'active',
  'trialing',
  'past_due',
  'unpaid',
  'paused',
])

/** When the paid period ends.
 *
 *  ⚠️ Stripe moved `current_period_end` off the Subscription and onto its
 *     items in the 2025 API versions. Both are read, newest shape first, so
 *     this keeps working across an API version bump instead of silently
 *     writing null and expiring everybody. */
export function periodEnd(sub: Stripe.Subscription): Date | null {
  const item = sub.items?.data?.[0] as { current_period_end?: number } | undefined
  const legacy = (sub as unknown as { current_period_end?: number }).current_period_end
  const secs = item?.current_period_end ?? legacy
  return typeof secs === 'number' ? new Date(secs * 1000) : null
}

/** The one subscription that decides the plan: a paid one over a live one over
 *  an ended one, then the one that runs longest, then the newest. Pure, so the
 *  rule can be read in one place and checked without Stripe. */
export function governing(subs: readonly Stripe.Subscription[]): Stripe.Subscription | null {
  const rank = (s: Stripe.Subscription) => (PAID.has(s.status) ? 2 : LIVE.has(s.status) ? 1 : 0)
  const ends = (s: Stripe.Subscription) => periodEnd(s)?.getTime() ?? 0
  let best: Stripe.Subscription | null = null
  for (const s of subs) {
    if (
      !best ||
      rank(s) > rank(best) ||
      (rank(s) === rank(best) && ends(s) > ends(best)) ||
      (rank(s) === rank(best) && ends(s) === ends(best) && s.created > best.created)
    ) {
      best = s
    }
  }
  return best
}

/** Every subscription on a customer, ended ones included. A customer has a
 *  handful at most; 100 is a page, not a limit anyone reaches. */
export async function subscriptionsOf(customerId: string): Promise<Stripe.Subscription[]> {
  const page = await stripe().subscriptions.list({ customer: customerId, status: 'all', limit: 100 })
  return page.data
}

/** The customer id an event points at, whatever kind of object it carries. */
export function customerOfEvent(event: Stripe.Event): string | null {
  const obj = event.data.object as { object?: string; id?: string; customer?: unknown }
  if (obj.object === 'customer') return obj.id ?? null
  const c = obj.customer
  if (typeof c === 'string') return c
  if (c && typeof c === 'object' && 'id' in c && typeof (c as { id: unknown }).id === 'string') {
    return (c as { id: string }).id
  }
  return null
}

/** Bring our two tables in line with what Stripe says about one customer.
 *
 *  Runs inside one transaction behind an advisory lock on the customer, and
 *  the Stripe reads happen INSIDE it. That is the exception to this repo's
 *  "no network inside a transaction" rule, and it is deliberate: the lock is
 *  the only thing held (no row is touched until the writes at the end), it
 *  covers one customer for well under a second, and its whole job is to make
 *  two concurrent events read-then-write one after the other — otherwise the
 *  slower handler can land an older read on top of a newer one.
 *
 *  ⚠️ `getSql()` is a one-connection pool. Everything in here must go through
 *     `tx`; a nested `getSql()` query would wait forever on the connection
 *     this transaction holds. */
export async function syncCustomer(customerId: string): Promise<string> {
  const sql = getSql()
  return sql.begin(async (tx) => {
    await tx`select pg_advisory_xact_lock(hashtextextended(${'stripe:' + customerId}, 0))`

    const customer = await stripe().customers.retrieve(customerId)
    const subs = customer.deleted ? [] : await subscriptionsOf(customerId)
    const sub = governing(subs)
    const paid = sub ? PAID.has(sub.status) : false
    const status = sub?.status ?? (customer.deleted ? 'customer_deleted' : 'none')
    const ends = sub ? periodEnd(sub) : null
    const notes: string[] = [`${customerId}: ${status} (${subs.length} sub${subs.length === 1 ? '' : 's'})`]

    // The address the grant is keyed on. `metadata.np_email` first: it is set
    // once at checkout and the customer cannot edit it, whereas `email` can be
    // changed in the billing portal — and a grant that follows an edited email
    // would leave the old address holding a paid, unclaimed grant.
    const email = customer.deleted
      ? null
      : (customer.metadata?.np_email || customer.email || '').toLowerCase() || null

    // 1 · The grant, keyed by email, written even with no account — that is
    //     the whole point of paying before signing up (db/047).
    if (email && sub) {
      await tx`
        insert into public.pro_grants
          (email, stripe_customer_id, stripe_subscription_id, plan_status, current_period_end)
        values (${email}, ${customerId}, ${sub.id}, ${status}, ${ends})
        on conflict (email) do update
          set stripe_customer_id     = excluded.stripe_customer_id,
              stripe_subscription_id = excluded.stripe_subscription_id,
              plan_status            = excluded.plan_status,
              current_period_end     = excluded.current_period_end,
              updated_at             = now()
      `
      notes.push(`grant ${email}`)
    }
    // Any other grant on this customer (an address from before a change) gets
    // the same state, so it can never be claimed as a paid grant it no longer is.
    await tx`
      update public.pro_grants
         set plan_status = ${status}, current_period_end = ${ends}, updated_at = now()
       where stripe_customer_id = ${customerId}
         and email is distinct from ${email}
    `

    // 2 · The profile, if an account exists: by the id we put on the customer,
    //     else by the customer id, else by the (Supabase-confirmed) email.
    const metaUser = customer.deleted ? '' : customer.metadata?.supabase_user_id ?? ''
    let [profile] = metaUser
      ? await tx<{ id: string; stripe_customer_id: string | null }[]>`
          select id, stripe_customer_id from public.profiles where id = ${metaUser}`
      : []
    if (!profile) {
      ;[profile] = await tx<{ id: string; stripe_customer_id: string | null }[]>`
        select id, stripe_customer_id from public.profiles where stripe_customer_id = ${customerId}`
    }
    if (!profile && email) {
      ;[profile] = await tx<{ id: string; stripe_customer_id: string | null }[]>`
        select id, stripe_customer_id from public.profiles where lower(email) = ${email}`
    }

    if (!profile) {
      notes.push('no account yet — waiting to be claimed')
    } else if (profile.stripe_customer_id && profile.stripe_customer_id !== customerId) {
      // The account already bills through a different customer, which is the
      // one that decides its plan. A stray customer found by email must not
      // be able to downgrade it.
      notes.push(`${profile.id} bills through ${profile.stripe_customer_id} — left alone`)
    } else {
      await tx`
        update public.profiles
           set plan                   = ${paid ? 'pro' : 'free'},
               plan_status            = ${status},
               stripe_customer_id     = ${customerId},
               stripe_subscription_id = ${sub?.id ?? null},
               current_period_end     = ${ends}
         where id = ${profile.id}
      `
      notes.push(`${profile.id} -> ${paid ? 'pro' : 'free'}`)
      // The grant has done its job; mark it so a later sign-up with the same
      // address cannot claim the same subscription a second time.
      if (email) {
        await tx`
          update public.pro_grants
             set claimed_by = ${profile.id}, claimed_at = coalesce(claimed_at, now())
           where email = ${email}
        `
      }
    }

    return notes.join(' · ')
  })
}

/** The address a completed Checkout Session paid with, for /welcome.
 *
 *  The success URL carries the session id instead of the email, so the page
 *  asks Stripe. Only a COMPLETE session answers — an id from an abandoned
 *  checkout says nothing — and anything malformed is refused before a call. */
export async function checkoutEmail(sessionId: string): Promise<string | null> {
  if (!/^cs_(test|live)_[A-Za-z0-9]{10,200}$/.test(sessionId)) return null
  try {
    const session = await stripe().checkout.sessions.retrieve(sessionId)
    if (session.status !== 'complete') return null
    return (session.customer_details?.email ?? session.customer_email ?? null)?.toLowerCase() ?? null
  } catch {
    return null
  }
}
