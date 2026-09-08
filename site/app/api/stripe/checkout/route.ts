/** Start a Stripe Checkout session for the signed-in user.
 *
 *  🔑 The Stripe customer is created once and remembered on the profile. Making
 *     a new one per checkout is the standard way to end up with a person who
 *     has three customer records, two of which the billing portal cannot see —
 *     so a user who upgrades, cancels, and upgrades again keeps one identity.
 */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { getSql } from '../../../lib/db'
import { stripe, stripeConfigured, PRICE_IDS, siteUrl, type BillingPeriod } from '../../../lib/stripe'

export const dynamic = 'force-dynamic'

export async function POST(request: Request) {
  if (!stripeConfigured()) {
    return NextResponse.json(
      { error: 'Paid plans are not switched on yet.' },
      { status: 503 }
    )
  }

  const user = await currentUser()
  if (!user) {
    return NextResponse.json({ error: 'Sign in first.' }, { status: 401 })
  }

  let period: BillingPeriod = 'monthly'
  try {
    const body = await request.json()
    if (body?.period === 'yearly') period = 'yearly'
  } catch {
    /* default to monthly */
  }

  const sql = getSql()
  const [profile] = await sql<{ stripe_customer_id: string | null }[]>`
    select stripe_customer_id from public.profiles where id = ${user.id}
  `

  let customerId = profile?.stripe_customer_id ?? null
  if (!customerId) {
    const customer = await stripe().customers.create({
      email: user.email ?? undefined,
      // The webhook arrives with a customer, not a session, so the link back to
      // our user has to live on the customer itself.
      metadata: { supabase_user_id: user.id },
    })
    customerId = customer.id
    await sql`
      update public.profiles set stripe_customer_id = ${customerId} where id = ${user.id}
    `
  }

  const session = await stripe().checkout.sessions.create({
    mode: 'subscription',
    customer: customerId,
    line_items: [{ price: PRICE_IDS[period], quantity: 1 }],
    success_url: `${siteUrl()}/account?upgraded=1`,
    cancel_url: `${siteUrl()}/pricing?cancelled=1`,
    allow_promotion_codes: true,
    // Belt and braces: the customer carries the id, and so does the
    // subscription, so neither a customer.* nor a subscription.* event needs a
    // lookup that could miss.
    subscription_data: { metadata: { supabase_user_id: user.id } },
    client_reference_id: user.id,
  })

  if (!session.url) {
    return NextResponse.json({ error: 'Stripe returned no checkout URL.' }, { status: 502 })
  }
  return NextResponse.json({ url: session.url })
}
