'use client'

/** What each plan is, and one honest sentence about what it is not.
 *
 *  🔑 The claim this page must never make is that Pro finds you an edge. The
 *     agent is paper, no arm is near its verdict gate, and that is written on
 *     /agent in a banner. What Pro removes is a limit on tools that measure —
 *     that is a real thing to sell and it is the only thing sold here.
 */

import { useState } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { useSession } from '../lib/useSession'

const MONTHLY_USD = 19
const YEARLY_USD = 190

type Period = 'monthly' | 'yearly'

const FREE_ROWS = [
  ['Scout — every football board on Polymarket', true],
  ['Live clock, score and book grade on every fixture', true],
  ['Game Center on any fixture', true],
  ['The agent’s full paper record', true],
  ['Lab — backtest a theory over 111,475 games', '3 a day'],
  ['Wallet — rebuild any trader’s record', '3 a day'],
  ['Watchlist, kept in this browser', true],
  ['Watchlist, synced across every device', false],
] as const

export default function PricingPage() {
  const { me } = useSession()
  const [period, setPeriod] = useState<Period>('monthly')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isPro = me?.plan === 'pro'
  const signedIn = Boolean(me?.user)

  async function upgrade() {
    setError(null)
    if (!signedIn) {
      window.location.href = '/login?next=%2Fpricing'
      return
    }
    setBusy(true)
    try {
      const r = await fetch('/api/stripe/checkout', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ period }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.error ?? 'Could not start checkout.')
      window.location.href = body.url
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start checkout.')
      setBusy(false)
    }
  }

  const price = period === 'monthly' ? MONTHLY_USD : YEARLY_USD
  const unit = period === 'monthly' ? '/month' : '/year'

  return (
    <AppShell>
      <div className="np-pricing">
        <header className="np-pricing-head">
          <h1>Two plans</h1>
          <p>
            The board is free and always will be — it is how anyone decides the site
            is worth an account. Pro removes the daily limit on the two tools that do
            real work: the Lab and the Wallet.
          </p>
        </header>

        <div className="np-pricing-toggle" role="group" aria-label="Billing period">
          <button
            className={period === 'monthly' ? 'is-on' : ''}
            onClick={() => setPeriod('monthly')}
          >
            Monthly
          </button>
          <button
            className={period === 'yearly' ? 'is-on' : ''}
            onClick={() => setPeriod('yearly')}
          >
            Yearly <span className="np-pricing-save">2 months free</span>
          </button>
        </div>

        <div className="np-pricing-grid">
          <section className="np-card np-plan">
            <h2 className="np-plan-name">Free</h2>
            <div className="np-plan-price"><span className="np-num">$0</span></div>
            <p className="np-plan-note">No card. An account takes an email and a password.</p>
            <ul className="np-plan-rows">
              {FREE_ROWS.map(([label, value]) => (
                <li key={label} className={value === false ? 'is-off' : ''}>
                  <span className="np-plan-mark">
                    {value === false ? '—' : value === true ? '✓' : ''}
                  </span>
                  <span>{label}</span>
                  {typeof value === 'string' && (
                    <b className="np-plan-qty np-num">{value}</b>
                  )}
                </li>
              ))}
            </ul>
            {!signedIn && (
              <Link href="/login?next=%2Fpricing" className="np-btn np-plan-cta">
                Create a free account
              </Link>
            )}
          </section>

          <section className="np-card np-plan is-pro">
            <h2 className="np-plan-name">
              Pro <span className="np-badge is-good">UNLIMITED</span>
            </h2>
            <div className="np-plan-price">
              <span className="np-num">${price}</span>
              <small>{unit}</small>
            </div>
            <p className="np-plan-note">
              {period === 'yearly'
                ? `$${(YEARLY_USD / 12).toFixed(2)} a month, billed once.`
                : 'Cancel any time, from Stripe’s own portal.'}
            </p>
            <ul className="np-plan-rows">
              <li><span className="np-plan-mark">✓</span><span>Everything in Free</span></li>
              <li><span className="np-plan-mark">✓</span><span>Lab — unlimited backtests</span></li>
              <li><span className="np-plan-mark">✓</span><span>Wallet — unlimited trader reads</span></li>
              <li><span className="np-plan-mark">✓</span><span>Watchlist synced across every device</span></li>
            </ul>
            {/* Alerts are built in the schema and NOT in the product: there is
                no email delivery yet, so they are named as what is next rather
                than listed as something bought. This site removed a newsletter
                form once for exactly this — it set local state, showed a tick,
                and sent nowhere. */}
            <p className="np-plan-next">
              <b>Next for Pro:</b> alerts when a fixture kicks off or its book
              first grades clean. Not built yet, and not part of what you are
              paying for today.
            </p>

            {isPro ? (
              <Link href="/account" className="np-btn np-plan-cta">You are on Pro — manage billing</Link>
            ) : (
              <button className="np-btn np-btn-primary np-plan-cta" onClick={upgrade} disabled={busy}>
                {busy ? 'Opening checkout…' : signedIn ? `Upgrade — $${price}${unit}` : 'Sign in to upgrade'}
              </button>
            )}
            {error && <p className="np-plan-error">{error}</p>}
          </section>
        </div>

        <div className="np-note np-pricing-honest">
          <strong>What Pro is not.</strong> It is not a tip service and it does not give
          you the agent’s picks to follow. The agent trades on paper, no strategy is
          near its verdict gate, and <Link href="/agent">its own tab says so</Link>.
          What you are paying for is unlimited use of tools that measure — a backtest
          against Pinnacle’s closing line, and a trader’s record rebuilt from their
          actual fills.
        </div>
      </div>
    </AppShell>
  )
}
