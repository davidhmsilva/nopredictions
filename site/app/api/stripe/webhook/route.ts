/** The only thing that grants or removes Pro.
 *
 *  Nothing in the browser can set a plan — `profiles` has no UPDATE policy for
 *  the client (db/044) — so this handler is the whole story of who is paying.
 *
 *  Three things it has to get right:
 *
 *  1. **The signature.** An unverified webhook endpoint is a public API for
 *     granting yourself a subscription. `constructEvent` is not optional, and
 *     it needs the RAW body — hence `request.text()` and not `request.json()`.
 *
 *  2. **Which user.** The link is carried on the Stripe objects themselves
 *     (`metadata.supabase_user_id`, set at checkout) with a lookup by
 *     `stripe_customer_id` as the fallback, because a subscription created by
 *     hand in the Stripe dashboard will have no metadata at all.
 *
 *  3. **Idempotency.** Stripe retries, and it does not promise order. Every
 *     write here is a full overwrite of the plan fields from the event's own
 *     state, so replaying an event changes nothing and a duplicate is free.
 *     ⚠️ Out-of-order is the harder half: a `deleted` arriving before a stale
 *     `updated` would resurrect a cancelled plan. That is why `deleted` writes
 *     `plan='free'` unconditionally and the status check below treats anything
 *     that is not active-or-trialing as not paid.
 */

import { NextResponse } from 'next/server'
import type Stripe from 'stripe'
import { getSql } from '../../../lib/db'
import { stripe, stripeConfigured, STRIPE_WEBHOOK_SECRET } from '../../../lib/stripe'

export const dynamic = 'force-dynamic'

/** Statuses that entitle someone to the product. `past_due` deliberately does
 *  not: Stripe is still retrying the card, and access resumes the moment it
 *  succeeds and sends another event. */
const PAID = new Set(['active', 'trialing'])

/** When the paid period ends.
 *
 *  ⚠️ Stripe moved `current_period_end` off the Subscription and onto its
 *     items in the 2025 API versions. Both are read, newest shape first, so
 *     this keeps working across an API version bump instead of silently
 *     writing null and expiring everybody. */
function periodEnd(sub: Stripe.Subscription): Date | null {
  const item = sub.items?.data?.[0] as { current_period_end?: number } | undefined
  const legacy = (sub as unknown as { current_period_end?: number }).current_period_end
  const secs = item?.current_period_end ?? legacy
  return typeof secs === 'number' ? new Date(secs * 1000) : null
}

function customerIdOf(sub: Stripe.Subscription): string | null {
  return typeof sub.customer === 'string' ? sub.customer : sub.customer?.id ?? null
}

async function userIdFor(sub: Stripe.Subscription): Promise<string | null> {
  const fromMeta = sub.metadata?.supabase_user_id
  if (fromMeta) return fromMeta

  const customerId = customerIdOf(sub)
  if (!customerId) return null

  const sql = getSql()
  const [row] = await sql<{ id: string }[]>`
    select id from public.profiles where stripe_customer_id = ${customerId}
  `
  return row?.id ?? null
}

/** The email the subscription belongs to.
 *
 *  Checkout puts it in metadata, so that is the cheap path. A subscription
 *  created by hand in the Stripe dashboard has none, and then the customer
 *  object is asked — one extra round trip, only on the rare path. */
async function emailFor(sub: Stripe.Subscription): Promise<string | null> {
  const fromMeta = sub.metadata?.np_email
  if (fromMeta) return fromMeta.toLowerCase()

  const customerId = customerIdOf(sub)
  if (!customerId) return null
  try {
    const customer = await stripe().customers.retrieve(customerId)
    if (customer.deleted) return null
    return customer.email?.toLowerCase() ?? null
  } catch {
    return null
  }
}

async function applySubscription(sub: Stripe.Subscription): Promise<string> {
  const paid = PAID.has(sub.status)
  const sql = getSql()
  const notes: string[] = []

  // 1 · The grant, keyed by email. Written FIRST and always, because the
  //     account may not exist yet — that is the whole point of paying before
  //     signing up (db/047). `handle_new_user` reads it when the account
  //     arrives, whenever that is.
  const email = await emailFor(sub)
  if (email) {
    await sql`
      insert into public.pro_grants
        (email, stripe_customer_id, stripe_subscription_id, plan_status, current_period_end)
      values (${email}, ${customerIdOf(sub)}, ${sub.id}, ${sub.status}, ${periodEnd(sub)})
      on conflict (email) do update
        set stripe_customer_id     = excluded.stripe_customer_id,
            stripe_subscription_id = excluded.stripe_subscription_id,
            plan_status            = excluded.plan_status,
            current_period_end     = excluded.current_period_end,
            updated_at             = now()
    `
    notes.push(`grant ${email} (${sub.status})`)
  } else {
    notes.push('no email on the subscription — no grant written')
  }

  // 2 · The profile, if an account exists. Found by id, and failing that by
  //     the email — someone who paid first and signed up later has a profile
  //     that the customer id was never written to.
  let userId = await userIdFor(sub)
  if (!userId && email) {
    const [row] = await sql<{ id: string }[]>`
      select id from public.profiles where lower(email) = ${email}
    `
    userId = row?.id ?? null
  }

  if (userId) {
    await sql`
      update public.profiles
         set plan                   = ${paid ? 'pro' : 'free'},
             plan_status            = ${sub.status},
             stripe_customer_id     = coalesce(stripe_customer_id, ${customerIdOf(sub)}),
             stripe_subscription_id = ${sub.id},
             current_period_end     = ${periodEnd(sub)}
       where id = ${userId}
    `
    notes.push(`${userId} -> ${paid ? 'pro' : 'free'}`)
    // The grant has done its job; mark it so a later sign-up with the same
    // address cannot claim the same subscription a second time.
    if (email) {
      await sql`
        update public.pro_grants
           set claimed_by = ${userId}, claimed_at = coalesce(claimed_at, now())
         where email = ${email}
      `
    }
  } else {
    notes.push('no account yet — waiting to be claimed')
  }

  return notes.join(' · ')
}

export async function POST(request: Request) {
  if (!stripeConfigured() || !STRIPE_WEBHOOK_SECRET) {
    // 503, not 200: an unconfigured endpoint that answers OK teaches Stripe
    // the event was delivered and it is never sent again.
    return NextResponse.json({ error: 'Stripe is not configured.' }, { status: 503 })
  }

  const signature = request.headers.get('stripe-signature')
  if (!signature) {
    return NextResponse.json({ error: 'Missing signature.' }, { status: 400 })
  }

  const raw = await request.text()
  let event: Stripe.Event
  try {
    event = stripe().webhooks.constructEvent(raw, signature, STRIPE_WEBHOOK_SECRET)
  } catch (err) {
    console.error('stripe webhook signature', err)
    return NextResponse.json({ error: 'Bad signature.' }, { status: 400 })
  }

  try {
    switch (event.type) {
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted': {
        const result = await applySubscription(event.data.object as Stripe.Subscription)
        console.log(`[stripe] ${event.type}: ${result}`)
        break
      }

      case 'checkout.session.completed': {
        // The subscription events carry everything this needs, and they always
        // follow. This case exists only for the race where they arrive first
        // and the customer row was not yet written — re-reading the
        // subscription makes the outcome the same either way.
        const session = event.data.object as Stripe.Checkout.Session
        const subId = typeof session.subscription === 'string' ? session.subscription : null
        if (subId) {
          const sub = await stripe().subscriptions.retrieve(subId)
          console.log(`[stripe] checkout.completed: ${await applySubscription(sub)}`)
        }
        break
      }

      default:
        // Everything else is noise for this product. Answering 200 stops
        // Stripe retrying an event we deliberately ignore.
        break
    }
  } catch (err) {
    console.error(`[stripe] handler failed for ${event.type}`, err)
    // 500 so Stripe retries. A swallowed failure here is someone who paid and
    // did not get the thing.
    return NextResponse.json({ error: 'Handler failed.' }, { status: 500 })
  }

  return NextResponse.json({ received: true })
}
