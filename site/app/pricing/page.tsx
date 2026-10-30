'use client'

/** What each plan is, and one honest sentence about what it is not.
 *
 *  🔑 The claim this page must never make is that Pro makes an agent win.
 *     Agents paper-trade and no strategy here has passed its verdict gate.
 *     What Pro sells is capacity — more agents built from your own words,
 *     kept and running at once — and that is the only thing sold here.
 *
 *  The two cards used to carry two independent lists, which meant the rows did
 *  not line up and nobody could actually compare the plans — the one job a
 *  pricing page has. The cards now carry only what defines each tier, and the
 *  comparison happens in one aligned table underneath.
 */

import { useState, type FormEvent } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { useSession } from '../lib/useSession'
import { AGENT_LIMITS, FREE_DAILY_LIMIT, PRICING, QUOTA_RESET_TEXT } from '../lib/planTerms'

// From planTerms, the same constants the server and Stripe read. This page
// used to type its own 19 and 190, and nothing kept the two in step.
const MONTHLY_USD = PRICING.monthlyUsd
const YEARLY_USD = PRICING.yearlyUsd

type Period = 'monthly' | 'yearly'

const FREE = AGENT_LIMITS.free
const PRO = AGENT_LIMITS.pro
const BUILDS = FREE_DAILY_LIMIT.lab

/** One row per thing that EXISTS. Anything planned goes under the table, named
 *  as planned — a matrix row that is a dash in both columns reads as "Pro does
 *  not get this either", which is true but is not what a comparison is for. */
const MATRIX: { k: string; free: string; pro: string; href?: string }[] = [
  { k: 'Build an agent from your own words — the AI writes the strategy and backtests it', free: `${BUILDS} a day`, pro: 'Unlimited', href: '/create' },
  { k: 'Simple agents from the picker, and the ready-made ones', free: '✓', pro: '✓', href: '/create' },
  { k: 'Agents you keep', free: String(FREE.saved), pro: String(PRO.saved) },
  { k: 'Agents running at the same time', free: String(FREE.running), pro: String(PRO.running) },
  { k: 'Copy agents from the Marketplace', free: '✓', pro: '✓', href: '/marketplace' },
  { k: 'Publish your agents to the Marketplace', free: '✓', pro: '✓', href: '/marketplace' },
  { k: 'Every bet, the profit curve and the closing-line value of each agent', free: '✓', pro: '✓', href: '/agent' },
]

/** The three lines on each card. Short on purpose: the card is the decision,
 *  the table below is the detail. */
const FREE_POINTS = [
  `${BUILDS} agents a day from your own words`,
  `Keep ${FREE.saved} agents, run ${FREE.running} at a time — copies included`,
  'No card, ever',
]
const PRO_POINTS = [
  'Everything in Free',
  'Build as many agents as you like',
  `Keep ${PRO.saved}, run ${PRO.running} at the same time`,
]

export default function PricingPage() {
  const { me } = useSession()
  const [period, setPeriod] = useState<Period>('monthly')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [email, setEmail] = useState('')

  const isPro = me?.plan === 'pro'
  const signedIn = Boolean(me?.user)

  /** 🔑 No account is required to pay.
   *
   *  This used to bounce a signed-out visitor to /login and back — asking for
   *  commitment before the product had been paid for. Now the email goes
   *  straight into Stripe, the webhook records the subscription against that
   *  address (db/047), and the account claims it whenever it is created.
   *  Signing in first still works and takes precedence; it is just no longer
   *  the toll gate. */
  async function upgrade(e?: FormEvent) {
    e?.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const r = await fetch('/api/stripe/checkout', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ period, email: signedIn ? undefined : email }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.error ?? 'Could not start checkout.')
      window.location.href = body.url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start checkout.')
      setBusy(false)
    }
  }

  const price = period === 'monthly' ? MONTHLY_USD : YEARLY_USD
  const unit = period === 'monthly' ? '/month' : '/year'

  return (
    <AppShell>
      <div className="pr-page">
        <header className="tp-head">
          <span className="tp-eyebrow">PRICING</span>
          <h1 className="tp-h1">Build agents free. Run more with Pro.</h1>
          <p className="tp-sub">
            A free account builds {BUILDS} agents a day from your own words and runs {FREE.running}{' '}
            at a time. Pro takes the daily limit off building and runs {PRO.running} at once.
          </p>
        </header>

        <div className="pr-toggle" role="group" aria-label="Billing period">
          <button
            className={period === 'monthly' ? 'is-on' : ''}
            onClick={() => setPeriod('monthly')}
            aria-pressed={period === 'monthly'}
          >
            Monthly
          </button>
          <button
            className={period === 'yearly' ? 'is-on' : ''}
            onClick={() => setPeriod('yearly')}
            aria-pressed={period === 'yearly'}
          >
            Yearly <span className="pr-save">2 months free</span>
          </button>
        </div>

        {/* ── the decision ── */}
        <div className="pr-cards">
          <section className="pr-card">
            <h2 className="pr-name">Free</h2>
            <div className="pr-price">
              <span className="np-num">$0</span>
            </div>
            <p className="pr-note">An email and a password. No card.</p>

            <ul className="pr-points">
              {FREE_POINTS.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>

            {signedIn ? (
              <div className="pr-cta-done">You are on this plan.</div>
            ) : (
              <Link href="/login?mode=signup&next=%2Fcreate" className="np-btn pr-cta">
                Create a free account
              </Link>
            )}
          </section>

          <section className="pr-card is-pro">
            <h2 className="pr-name">
              Pro <span className="np-badge">Unlimited</span>
            </h2>
            <div className="pr-price">
              <span className="np-num">${price}</span>
              <small>{unit}</small>
            </div>
            <p className="pr-note">
              {period === 'yearly'
                ? `$${(YEARLY_USD / 12).toFixed(2)} a month, billed once. Cancel any time.`
                : 'Cancel any time, from Stripe’s own portal.'}{' '}
              <Link href="/refunds">14-day money-back guarantee.</Link>
            </p>

            <ul className="pr-points">
              {PRO_POINTS.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>

            {isPro ? (
              <Link href="/account" className="np-btn pr-cta">
                You are on Pro — manage billing
              </Link>
            ) : signedIn ? (
              <>
                <button className="np-btn np-btn-primary pr-cta" onClick={() => upgrade()} disabled={busy}>
                  {busy ? 'Opening checkout…' : `Upgrade — $${price}${unit}`}
                </button>
                <p className="pr-buy-note">
                  By upgrading you agree to the <Link href="/terms">Terms</Link> and{' '}
                  <Link href="/privacy">Privacy Policy</Link>.
                </p>
              </>
            ) : (
              <form className="pr-buy" onSubmit={upgrade}>
                <label className="pr-buy-label" htmlFor="pr-email">
                  Your email — no account needed
                </label>
                <input
                  id="pr-email"
                  className="np-input"
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(ev) => setEmail(ev.target.value)}
                  placeholder="you@example.com"
                  disabled={busy}
                />
                <button className="np-btn np-btn-primary pr-cta" type="submit" disabled={busy}>
                  {busy ? 'Opening checkout…' : `Continue to Stripe — $${price}${unit}`}
                </button>
                <p className="pr-buy-note">
                  Pay first, pick a password after. Already have an account?{' '}
                  <Link href="/login?next=%2Fpricing">Sign in</Link> and it goes on that one.
                </p>
                <p className="pr-buy-note">
                  By continuing you agree to the <Link href="/terms">Terms</Link> and{' '}
                  <Link href="/privacy">Privacy Policy</Link>.
                </p>
              </form>
            )}
            {error && <p className="np-form-error">{error}</p>}
          </section>
        </div>

        {/* ── the comparison, aligned so it can actually be read across ── */}
        <section className="pr-compare">
          <div className="tp-section-head">
            <h2>Line by line</h2>
          </div>
          <div className="pr-table">
            <div className="pr-row is-head">
              <span />
              <span>Free</span>
              <span>Pro</span>
            </div>
            {MATRIX.map((m) => (
              <div key={m.k} className="pr-row">
                <span className="pr-feat">
                  {m.href ? <Link href={m.href}>{m.k}</Link> : m.k}
                </span>
                <span className={`pr-val${m.free === '✓' ? ' is-tick' : ''}`}>{m.free}</span>
                <span className={`pr-val${m.pro === '✓' ? ' is-tick' : ''} is-pro`}>{m.pro}</span>
              </div>
            ))}
          </div>
          <p className="pr-reset">
            Free builds reset at {QUOTA_RESET_TEXT}. A refused build never costs you one — if
            it fails on our side, you get it back.
          </p>
        </section>

        {/* ⚠️ Alerts are in the schema (db/044) and nothing sends anything. They
            are named here as planned rather than sitting in the table, because a
            matrix row that is a dash in both columns reads as a Pro limitation
            instead of an unbuilt feature. */}
        <div className="pr-next">
          <strong>Coming later, and not part of Pro today:</strong> an email when one of your
          agents places a bet. When it arrives it goes to Pro, and it&apos;ll move into the table
          above.
        </div>

        <div className="np-note pr-honest">
          <strong>What Pro is not.</strong> It isn&apos;t a tip service, and it doesn&apos;t make an
          agent profitable. Agents paper-trade — practice bets at real prices — and we never place
          real bets or hold your money. You&apos;re paying for more agents: built from your own
          words, kept, and running at the same time.
        </div>
      </div>
    </AppShell>
  )
}
