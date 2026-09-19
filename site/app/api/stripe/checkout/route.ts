/** Start a Stripe Checkout session.
 *
 *  🔑 **No account is required to pay.** The old flow was: create an account,
 *     then upgrade — two steps, the first of which asks for commitment before
 *     the product has been paid for. Now an email is enough: it goes straight
 *     into Stripe, the webhook records the grant against that address
 *     (db/047), and the account claims it whenever it is created. Pay then
 *     sign up, or sign up then pay; both end in the same place.
 *
 *  🔑 The Stripe customer is created once and remembered — on the profile for
 *     someone signed in, and found by email for someone who is not. Making a
 *     new one per checkout is the standard way to end up with a person who has
 *     three customer records, two of which the billing portal cannot see.
 */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { getSql } from '../../../lib/db'
import { LIVE, subscriptionsOf } from '../../../lib/billing'
import { stripe, stripeConfigured, PRICE_IDS, siteUrl, type BillingPeriod } from '../../../lib/stripe'

export const dynamic = 'force-dynamic'

/** Same shape the newsletter and the table's CHECK constraint use. */
const EMAIL =
  /^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$/

export async function POST(request: Request) {
  if (!stripeConfigured()) {
    return NextResponse.json(
      { error: 'Paid plans are not switched on yet.' },
      { status: 503 }
    )
  }

  let period: BillingPeriod = 'monthly'
  let typedEmail = ''
  try {
    const body = await request.json()
    if (body?.period === 'yearly') period = 'yearly'
    typedEmail = String(body?.email ?? '').trim().toLowerCase()
  } catch {
    /* defaults */
  }

  const user = await currentUser()
  // A signed-in session always wins over a typed address. Otherwise someone
  // could pay for their own account while typing someone else's email into the
  // box, and the grant would land on the wrong person.
  const email = user?.email?.toLowerCase() ?? typedEmail

  if (!email || !EMAIL.test(email)) {
    return NextResponse.json(
      { error: 'Enter the email address you want the subscription on.' },
      { status: 400 }
    )
  }

  const sql = getSql()

  // Find an existing Stripe customer for this person: by account if signed in,
  // by a previous grant if not.
  let customerId: string | null = null
  if (user) {
    const [p] = await sql<{ stripe_customer_id: string | null }[]>`
      select stripe_customer_id from public.profiles where id = ${user.id}
    `
    customerId = p?.stripe_customer_id ?? null
  }
  if (!customerId) {
    const [g] = await sql<{ stripe_customer_id: string | null }[]>`
      select stripe_customer_id from public.pro_grants where email = ${email}
    `
    customerId = g?.stripe_customer_id ?? null
  }

  // Then Stripe itself. A grant only exists once a webhook has landed, so two
  // checkouts opened before the first one's webhook would otherwise make two
  // customers for one person — and the billing portal only ever shows one.
  // (Stripe's email filter is case-sensitive; every customer made here is
  // created lower-cased, which is what this looks up.)
  if (!customerId) {
    const found = await stripe().customers.list({ email, limit: 1 })
    customerId = found.data[0]?.id ?? null
  }

  if (!customerId) {
    const customer = await stripe().customers.create({
      email,
      // The webhook arrives with a customer, not a session. The link back has
      // to travel on the objects themselves — the user id when we have one,
      // and the email always, because the email is what db/047 keys on.
      metadata: {
        supabase_user_id: user?.id ?? '',
        np_email: email,
      },
    })
    customerId = customer.id
  } else {
    // 🔑 One subscription per person. A second checkout on top of a live
    //    subscription is a second charge, and the plan sync would then have
    //    two to reconcile. Someone signed in is sent to the portal, where
    //    switching monthly to yearly is a change and not a new subscription.
    //    ⚠️ The anonymous refusal does say that the address has a live
    //    subscription. The alternative is taking a second payment from them.
    const live = (await subscriptionsOf(customerId)).some((s) => LIVE.has(s.status))
    if (live) {
      if (!user) {
        return NextResponse.json(
          { error: 'That email already has a subscription. Sign in with it to manage billing.' },
          { status: 409 }
        )
      }
      const portal = await stripe().billingPortal.sessions.create({
        customer: customerId,
        return_url: `${siteUrl()}/account`,
      })
      return NextResponse.json({ url: portal.url, already: true })
    }
  }

  if (user) {
    // Remember it on the account, unless the account already has one or the
    // customer already belongs to another account (the column is unique).
    await sql`
      update public.profiles set stripe_customer_id = ${customerId}
       where id = ${user.id}
         and stripe_customer_id is null
         and not exists (select 1 from public.profiles where stripe_customer_id = ${customerId})
    `
  }

  const session = await stripe().checkout.sessions.create({
    mode: 'subscription',
    customer: customerId,
    line_items: [{ price: PRICE_IDS[period], quantity: 1 }],
    // `/welcome` rather than `/account`: someone who has just paid without an
    // account cannot read an account page, and sending them to one that says
    // "not signed in" immediately after taking their money is the worst
    // possible first screen.
    // The session id, never the email: a query string ends up in request
    // logs and browser history. /welcome reads the address back from Stripe.
    success_url: `${siteUrl()}/welcome?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${siteUrl()}/pricing?cancelled=1`,
    allow_promotion_codes: true,
    subscription_data: {
      metadata: { supabase_user_id: user?.id ?? '', np_email: email },
    },
    client_reference_id: user?.id ?? email,
  })

  if (!session.url) {
    return NextResponse.json({ error: 'Stripe returned no checkout URL.' }, { status: 502 })
  }
  return NextResponse.json({ url: session.url })
}
