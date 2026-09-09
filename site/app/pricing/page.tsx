'use client'

/** What each plan is, and one honest sentence about what it is not.
 *
 *  🔑 The claim this page must never make is that Pro finds you an edge. The
 *     agent is paper, no arm is near its verdict gate, and that is written on
 *     /agent/ours in a banner. What Pro removes is a limit on tools that
 *     MEASURE — that is a real thing to sell, and it is the only thing sold
 *     here.
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

const MONTHLY_USD = 19
const YEARLY_USD = 190

type Period = 'monthly' | 'yearly'

/** One row per thing that EXISTS. Anything planned goes under the table, named
 *  as planned — a matrix row that is a dash in both columns reads as "Pro does
 *  not get this either", which is true but is not what a comparison is for. */
const MATRIX: { k: string; free: string; pro: string; href?: string }[] = [
  { k: 'Scout — every football board on Polymarket', free: '✓', pro: '✓', href: '/' },
  { k: 'Live clock, score and book quality on every fixture', free: '✓', pro: '✓' },
  { k: 'Game Center on any fixture', free: '✓', pro: '✓' },
  { k: 'Dropping odds — where the money went', free: '✓', pro: '✓', href: '/dropping-odds' },
  { k: 'Insights — what we measured, including the failures', free: '✓', pro: '✓', href: '/insights' },
  { k: 'Our agent’s full paper record', free: '✓', pro: '✓', href: '/agent/ours' },
  { k: 'Lab — a theory replayed over 111,475 games', free: '3 a day', pro: 'Unlimited', href: '/lab' },
  { k: 'Wallet — any trader’s record rebuilt from their fills', free: '3 a day', pro: 'Unlimited', href: '/wallet' },
  { k: 'Watchlist', free: 'This browser', pro: 'Every device' },
]

/** The three lines on each card. Short on purpose: the card is the decision,
 *  the table below is the detail. */
const FREE_POINTS = [
  'The whole board, the Game Center, the movers and the record',
  'Three Lab tests and three wallet reads a day',
  'No card, ever',
]
const PRO_POINTS = [
  'Everything in Free',
  'Lab and Wallet with no daily limit',
  'Watchlist that follows you to any device',
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
          <h1 className="tp-h1">The board is free. The tools have a limit.</h1>
          <p className="tp-sub">
            Everything you can look at costs nothing and always will — it is how
            anyone decides this site is worth an account. What Pro removes is the
            daily limit on the two things that do real work.
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
              <Link href="/login?mode=signup&next=%2Flab" className="np-btn pr-cta">
                Create a free account
              </Link>
            )}
          </section>

          <section className="pr-card is-pro">
            <h2 className="pr-name">
              Pro <span className="np-badge is-good">NO LIMIT</span>
            </h2>
            <div className="pr-price">
              <span className="np-num">${price}</span>
              <small>{unit}</small>
            </div>
            <p className="pr-note">
              {period === 'yearly'
                ? `$${(YEARLY_USD / 12).toFixed(2)} a month, billed once. Cancel any time.`
                : 'Cancel any time, from Stripe’s own portal.'}
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
              <button className="np-btn np-btn-primary pr-cta" onClick={() => upgrade()} disabled={busy}>
                {busy ? 'Opening checkout…' : `Upgrade — $${price}${unit}`}
              </button>
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
            Free counts reset at 00:00 UTC. A refused run never costs you one — if a
            test fails on our side, you get it back.
          </p>
        </section>

        {/* ⚠️ Alerts are in the schema (db/044) and nothing sends anything. They
            are named here as planned rather than sitting in the table, because a
            matrix row that is a dash in both columns reads as a Pro limitation
            instead of an unbuilt feature. */}
        <div className="pr-next">
          <strong>Not built yet, and not part of what you would be paying for:</strong>{' '}
          alerts when a fixture kicks off or its book first grades clean. When they
          exist they go to Pro, and this page will move them into the table above.
        </div>

        <div className="np-note pr-honest">
          <strong>What Pro is not.</strong> It is not a tip service and it does not give
          you the agent’s picks to follow. The agent trades on paper, no strategy is
          near its verdict gate, and{' '}
          <Link href="/agent/ours">its own record says so</Link>. What you are paying
          for is unlimited use of tools that measure — a backtest against Pinnacle’s
          closing line, and a trader’s record rebuilt from their actual fills.
        </div>
      </div>
    </AppShell>
  )
}
