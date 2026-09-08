/** Send a paying user to Stripe's own billing portal.
 *
 *  Cancelling, changing card, downloading an invoice, switching monthly to
 *  yearly — all of it is Stripe's UI, not ours. Building any of that here
 *  would mean holding card state we have no reason to hold.
 */

import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { getSql } from '../../../lib/db'
import { stripe, stripeConfigured, siteUrl } from '../../../lib/stripe'

export const dynamic = 'force-dynamic'

export async function POST() {
  if (!stripeConfigured()) {
    return NextResponse.json({ error: 'Paid plans are not switched on yet.' }, { status: 503 })
  }

  const user = await currentUser()
  if (!user) return NextResponse.json({ error: 'Sign in first.' }, { status: 401 })

  const sql = getSql()
  const [profile] = await sql<{ stripe_customer_id: string | null }[]>`
    select stripe_customer_id from public.profiles where id = ${user.id}
  `
  if (!profile?.stripe_customer_id) {
    return NextResponse.json({ error: 'No billing account yet.' }, { status: 400 })
  }

  const session = await stripe().billingPortal.sessions.create({
    customer: profile.stripe_customer_id,
    return_url: `${siteUrl()}/account`,
  })
  return NextResponse.json({ url: session.url })
}
