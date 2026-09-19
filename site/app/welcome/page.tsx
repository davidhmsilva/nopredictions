/** /welcome — Stripe's return URL after a successful checkout.
 *
 *  The success URL carries `?session_id=cs_…` rather than the email, so the
 *  address is read back from Stripe here, on the server, and handed to the
 *  client half as a prop. A missing, malformed or unfinished session simply
 *  shows no address ("the address you paid with"), never an error: the payment
 *  is already recorded by the webhook, whatever this page manages to read.
 */

import { AppShell } from '../components/AppShell'
import { checkoutEmail } from '../lib/billing'
import { stripeConfigured } from '../lib/stripe'
import { WelcomeClient } from './WelcomeClient'

export const dynamic = 'force-dynamic'

export default async function WelcomePage({
  searchParams,
}: {
  searchParams: { session_id?: string | string[] }
}) {
  const raw = searchParams.session_id
  const sessionId = (Array.isArray(raw) ? raw[0] : raw) ?? ''
  const email = stripeConfigured() && sessionId ? await checkoutEmail(sessionId) : null

  return (
    <AppShell>
      <WelcomeClient email={email} />
    </AppShell>
  )
}
