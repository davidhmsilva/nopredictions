'use client'

/** Where Stripe sends someone back after a successful payment.
 *
 *  🔑 It exists because `/account` is the wrong page for the common case. In
 *     the pay-first funnel most people arriving here have just paid and have
 *     no account at all, and an account page that says "not signed in"
 *     immediately after taking someone's money is the worst possible first
 *     screen.
 *
 *  The subscription is already safe: the webhook recorded it against the email
 *  (db/047) before this page loaded, and it is claimed by the account whenever
 *  it is created — today, tomorrow, or next week. So the one instruction here
 *  is "use the same address", and everything else is reassurance.
 */

import { Suspense, useEffect } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { invalidateSession, useSession } from '../lib/useSession'

function WelcomeInner() {
  const params = useSearchParams()
  const router = useRouter()
  const email = (params.get('email') ?? '').trim()
  const { me } = useSession()

  // Someone who was already signed in when they paid needs no instructions —
  // the webhook has flipped their profile, and the only thing between them and
  // the product is a stale session read. Drop it and send them on.
  useEffect(() => {
    if (me?.plan === 'pro') {
      invalidateSession()
      const t = setTimeout(() => router.push('/account'), 1200)
      return () => clearTimeout(t)
    }
  }, [me?.plan, router])

  const signedIn = Boolean(me?.user)

  return (
    <div className="np-welcome">
      <div className="np-card np-welcome-card">
        <span className="np-badge is-good">PAYMENT RECEIVED</span>
        <h1>You are on Pro.</h1>

        {signedIn ? (
          <>
            <p>
              It is on your account already. Taking you to the billing page — or{' '}
              <Link href="/account">go there now</Link>.
            </p>
          </>
        ) : (
          <>
            <p>
              The subscription is held against{' '}
              <strong>{email || 'the address you paid with'}</strong>. Create an
              account with that same address and it is applied the moment you do.
            </p>
            <Link
              className="np-btn np-btn-primary np-welcome-cta"
              href={`/login?mode=signup&next=%2Faccount${email ? `&email=${encodeURIComponent(email)}` : ''}`}
            >
              Pick a password and finish
            </Link>
            <p className="np-welcome-note">
              No rush — the subscription is recorded and waits for you. If you
              use a different address it will land on the wrong account, so use
              the one you paid with. Nothing was emailed to you: there is no
              mailer wired up yet, and this page will not pretend otherwise.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

export default function WelcomePage() {
  return (
    <AppShell>
      <Suspense fallback={<div className="np-welcome" />}>
        <WelcomeInner />
      </Suspense>
    </AppShell>
  )
}
