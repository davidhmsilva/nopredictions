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
 *  2. **Order.** Stripe retries, duplicates and reorders. So no event's payload
 *     is ever written: each one only names a customer, and `syncCustomer`
 *     (lib/billing.ts) writes what Stripe says about that customer NOW. A late
 *     `updated` after a `deleted` re-reads "canceled" and changes nothing.
 *     That also makes the endpoint's API version irrelevant to what is stored,
 *     since the state is always read back through the pinned SDK.
 *
 *  3. **Failures are retried.** Anything that throws answers 500, and Stripe
 *     sends the event again. A swallowed failure is someone who paid and did
 *     not get the thing.
 *
 *  Subscribe the endpoint to exactly the events in HANDLED below.
 */

import { NextResponse } from 'next/server'
import type Stripe from 'stripe'
import { customerOfEvent, syncCustomer } from '../../../lib/billing'
import { stripe, stripeConfigured, STRIPE_WEBHOOK_SECRET } from '../../../lib/stripe'

export const dynamic = 'force-dynamic'

/** Events that can change who is entitled to Pro. `invoice.*` matters on its
 *  own: a renewal that fails turns `active` into `past_due` and one that
 *  recovers turns it back, and those are the events Stripe guarantees. */
const HANDLED: ReadonlySet<string> = new Set([
  'checkout.session.completed',
  'customer.subscription.created',
  'customer.subscription.updated',
  'customer.subscription.deleted',
  'customer.subscription.paused',
  'customer.subscription.resumed',
  'invoice.paid',
  'invoice.payment_failed',
])

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

  // Everything else is noise for this product. Answering 200 stops Stripe
  // retrying an event we deliberately ignore.
  if (!HANDLED.has(event.type)) {
    return NextResponse.json({ received: true, ignored: event.type })
  }

  const customerId = customerOfEvent(event)
  if (!customerId) {
    // A checkout that never created a customer, or an invoice with none. There
    // is nobody to sync, and retrying would not produce one.
    console.log(`[stripe] ${event.type} ${event.id}: no customer — nothing to sync`)
    return NextResponse.json({ received: true })
  }

  try {
    console.log(`[stripe] ${event.type} ${event.id}: ${await syncCustomer(customerId)}`)
  } catch (err) {
    console.error(`[stripe] sync failed for ${event.type} ${event.id}`, err)
    return NextResponse.json({ error: 'Handler failed.' }, { status: 500 })
  }

  return NextResponse.json({ received: true })
}
