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
    if (user) {
      await sql`update public.profiles set stripe_customer_id = ${customerId} where id = ${user.id}`
    }
  }

  const session = await stripe().checkout.sessions.create({
    mode: 'subscription',
    customer: customerId,
    line_items: [{ price: PRICE_IDS[period], quantity: 1 }],
    // `/welcome` rather than `/account`: someone who has just paid without an
    // account cannot read an account page, and sending them to one that says
    // "not signed in" immediately after taking their money is the worst
    // possible first screen.
    success_url: `${siteUrl()}/welcome?email=${encodeURIComponent(email)}`,
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
