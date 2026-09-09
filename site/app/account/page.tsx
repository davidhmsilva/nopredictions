'use client'

/** The account: who you are, what plan you are on, what is left today, and the
 *  two buttons that change any of it.
 *
 *  Billing lives entirely in Stripe's portal. Cancelling, changing card and
 *  invoices are all one link away and none of it is reimplemented here.
 */

import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { supabaseBrowser } from '../lib/supabaseBrowser'
import { useSession, invalidateSession, type Quota } from '../lib/useSession'

function QuotaRow({ label, q, href }: { label: string; q: Quota | null; href: string }) {
  if (!q) return null
  const unlimited = q.limit === null
  return (
    <li className="np-acct-quota">
      <Link href={href}>{label}</Link>
      {unlimited ? (
        <b className="np-badge is-good">UNLIMITED</b>
      ) : (
        <b className={`np-num ${q.remaining === 0 ? 'c-red' : ''}`}>
          {q.remaining} of {q.limit} left today
        </b>
      )}
    </li>
  )
}

function AccountPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const { me, loading } = useSession()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const justUpgraded = params.get('upgraded') === '1'

  async function signOut() {
    await supabaseBrowser().auth.signOut()
    invalidateSession()
    router.push('/')
    router.refresh()
  }

  async function manageBilling() {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/api/stripe/portal', { method: 'POST' })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.error ?? 'Could not open the billing portal.')
      window.location.href = body.url
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the billing portal.')
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <AppShell>
        <div className="np-acct"><p className="np-acct-loading">Reading your account…</p></div>
      </AppShell>
    )
  }

  if (!me?.user) {
    return (
      <AppShell>
        <div className="np-acct">
          <h1>Not signed in</h1>
          <p className="np-acct-sub">
            Scout, Agent and the Game Center work without an account. The Lab and the
            Wallet need one.
          </p>
          <Link href="/login?next=%2Faccount" className="np-btn np-btn-primary">Sign in</Link>
        </div>
      </AppShell>
    )
  }

  const isPro = me.plan === 'pro'

  return (
    <AppShell>
      <div className="np-acct">
        <h1>Account</h1>

        {justUpgraded && (
          <div className="np-acct-ok">
            Payment received. If the plan below still says Free, Stripe’s confirmation
            is a second or two behind — reload once.
          </div>
        )}

        <section className="np-card np-acct-card">
          <dl className="np-acct-dl">
            <dt>Email</dt>
            <dd>{me.user.email ?? '—'}</dd>
            <dt>Plan</dt>
            <dd>
              {isPro ? <span className="np-badge is-good">PRO</span> : <span className="np-badge">FREE</span>}
            </dd>
          </dl>

          <ul className="np-acct-quotas">
            <QuotaRow label="Lab" q={me.lab} href="/lab" />
            <QuotaRow label="Wallet" q={me.wallet} href="/wallet" />
          </ul>
          {!isPro && (
            <p className="np-acct-reset">Free counts reset at 00:00 UTC.</p>
          )}

          <div className="np-acct-actions">
            {isPro ? (
              <button className="np-btn" onClick={manageBilling} disabled={busy}>
                {busy ? 'Opening…' : 'Manage billing'}
              </button>
            ) : (
              <Link href="/pricing" className="np-btn np-btn-primary">See Pro</Link>
            )}
            <button className="np-btn" onClick={signOut}>Sign out</button>
          </div>
          {error && <p className="np-acct-error">{error}</p>}
        </section>
      </div>
    </AppShell>
  )
}

/** `useSearchParams` makes this page client-rendered by definition, and Next
 *  refuses to prerender it without a boundary to fall back to. Neither page
 *  has anything to gain from static HTML — both are about one specific
 *  person — so the boundary is the whole answer rather than a workaround. */
export default function AccountPage() {
  return (
    <Suspense fallback={<div className="np-acct"><p className="np-acct-loading">Reading your account…</p></div>}>
      <AccountPageInner />
    </Suspense>
  )
}
