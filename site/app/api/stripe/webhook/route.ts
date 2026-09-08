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

async function userIdFor(sub: Stripe.Subscription): Promise<string | null> {
  const fromMeta = sub.metadata?.supabase_user_id
  if (fromMeta) return fromMeta

  const customerId = typeof sub.customer === 'string' ? sub.customer : sub.customer?.id
  if (!customerId) return null

  const sql = getSql()
  const [row] = await sql<{ id: string }[]>`
    select id from public.profiles where stripe_customer_id = ${customerId}
  `
  return row?.id ?? null
}

async function applySubscription(sub: Stripe.Subscription): Promise<string> {
  const userId = await userIdFor(sub)
  if (!userId) return 'no matching profile'

  const paid = PAID.has(sub.status)
  const sql = getSql()
  await sql`
    update public.profiles
       set plan                   = ${paid ? 'pro' : 'free'},
           plan_status            = ${sub.status},
           stripe_subscription_id = ${sub.id},
           current_period_end     = ${periodEnd(sub)}
     where id = ${userId}
  `
  return `${userId} -> ${paid ? 'pro' : 'free'} (${sub.status})`
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
