'use client'

/** The account: who you are, what plan you are on, what is left today, and the
 *  three buttons that change any of it.
 *
 *  Billing lives entirely in Stripe's portal. Cancelling, changing card and
 *  invoices are all one link away and none of it is reimplemented here — this
 *  page holds no card details because it never sees any.
 *
 *  The quota used to be a line of text: "2 of 3 left today". It is a bar now,
 *  because the one question someone opens this page with is how much is left,
 *  and a bar answers it before the sentence is read.
 */

import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { supabaseBrowser } from '../lib/supabaseBrowser'
import { useSession, invalidateSession, type Quota } from '../lib/useSession'

/** One metered tool, with what is left drawn rather than described. */
function Meter({ label, href, q }: { label: string; href: string; q: Quota | null }) {
  if (!q) return null

  if (q.limit === null) {
    return (
      <div className="ac-meter is-unlimited">
        <div className="ac-meter-top">
          <Link href={href}>{label}</Link>
          <span className="np-badge is-good">NO LIMIT</span>
        </div>
      </div>
    )
  }

  const used = Math.min(q.used, q.limit)
  const pct = q.limit > 0 ? (used / q.limit) * 100 : 0
  const out = q.remaining === 0

  return (
    <div className={`ac-meter${out ? ' is-out' : ''}`}>
      <div className="ac-meter-top">
        <Link href={href}>{label}</Link>
        <span className="np-num ac-meter-count">
          {q.remaining} of {q.limit} left
        </span>
      </div>
      <div
        className="ac-bar"
        role="progressbar"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={q.limit}
        aria-label={`${label}: ${used} of ${q.limit} used today`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function AccountInner() {
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
      <div className="ac-page">
        <p className="ac-loading">Reading your account…</p>
      </div>
    )
  }

  if (!me?.user) {
    return (
      <div className="ac-page">
        <header className="tp-head">
          <span className="tp-eyebrow">ACCOUNT</span>
          <h1 className="tp-h1">You are not signed in.</h1>
          <p className="tp-sub">
            Scout, Dropping odds, the Game Center and the agent’s record all work
            without an account. The Lab and the Wallet need one — three of each a
            day, free, no card.
          </p>
        </header>
        <div className="ac-actions">
          <Link href="/login?next=%2Faccount" className="np-btn np-btn-primary">Sign in</Link>
          <Link href="/login?mode=signup&next=%2Faccount" className="np-btn">Create an account</Link>
        </div>
      </div>
    )
  }

  const isPro = me.plan === 'pro'

  return (
    <div className="ac-page">
      <header className="tp-head">
        <span className="tp-eyebrow">ACCOUNT</span>
        <h1 className="tp-h1">{me.user.email ?? 'Your account'}</h1>
      </header>

      {justUpgraded && (
        <div className="ac-ok">
          Payment received. If the plan below still says Free, Stripe’s confirmation
          is a second or two behind — reload once.
        </div>
      )}

      <div className="ac-grid">
        {/* ── plan ── */}
        <section className={`ac-card${isPro ? ' is-pro' : ''}`}>
          <div className="ac-card-head">
            <h2>Plan</h2>
            {isPro ? (
              <span className="np-badge is-good">PRO</span>
            ) : (
              <span className="np-badge">FREE</span>
            )}
          </div>

          <p className="ac-card-note">
            {isPro
              ? 'No daily limit on the Lab or the Wallet, and a watchlist that follows you to any device.'
              : 'Three Lab tests and three wallet reads a day. The board, the movers and the record are unlimited and always will be.'}
          </p>

          <div className="ac-card-actions">
            {isPro ? (
              <button className="np-btn" onClick={manageBilling} disabled={busy}>
                {busy ? 'Opening…' : 'Manage billing ↗'}
              </button>
            ) : (
              <Link href="/pricing" className="np-btn np-btn-primary">See Pro</Link>
            )}
          </div>
          {isPro && (
            <p className="ac-card-foot">
              Cancelling, changing card and invoices are all in Stripe’s own portal.
              This page holds no card details because it never sees any.
            </p>
          )}
          {error && <p className="np-form-error">{error}</p>}
        </section>

        {/* ── usage ── */}
        <section className="ac-card">
          <div className="ac-card-head">
            <h2>Today</h2>
            {!isPro && <span className="ac-reset np-num">resets 00:00 UTC</span>}
          </div>

          <div className="ac-meters">
            <Meter label="Lab" href="/lab" q={me.lab} />
            <Meter label="Wallet" href="/wallet" q={me.wallet} />
          </div>

          {!isPro && (
            <p className="ac-card-foot">
              A run that fails on our side never costs you one — it is given back.
            </p>
          )}
        </section>
      </div>

      <div className="ac-actions">
        <button className="np-btn" onClick={signOut}>Sign out</button>
      </div>
    </div>
  )
}

export default function AccountPage() {
  return (
    <AppShell>
      {/* `useSearchParams` (for ?upgraded=1) makes this client-rendered by
          definition, and Next refuses to prerender it without a boundary. */}
      <Suspense fallback={<div className="ac-page"><p className="ac-loading">Reading your account…</p></div>}>
        <AccountInner />
      </Suspense>
    </AppShell>
  )
}
