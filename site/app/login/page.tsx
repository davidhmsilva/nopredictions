'use client'

/** Sign in and sign up, on one form.
 *
 *  Three paths, in the order they cost the user something:
 *
 *    Google        one click, no password — shown only when the provider is
 *                  actually configured, because a button that opens a Supabase
 *                  error page is worse than no button.
 *    Magic link    no password to remember, but it needs email delivery that
 *                  works. Same rule: shown only when switched on.
 *    Password      always available, because it needs nothing but the database.
 *
 *  ⚠️ Sign-up has two possible outcomes and the difference is a Supabase
 *     setting we do not control from here. With email confirmation ON the
 *     response carries a user and NO session — the account exists but is not
 *     usable until a link is clicked. With it OFF, a session comes back and the
 *     user is in. Both are handled; the form says which happened rather than
 *     showing a spinner that never resolves.
 *
 *  The page is a two-column split. The card used to float alone in the middle
 *  of a 1280px page under a 22px heading, saying nothing about what an account
 *  is for and looking like a different site. The left column now carries the
 *  reason and the card keeps the job. ⚠️ The card is FIRST in the DOM — on a
 *  phone the person came to sign in, not to read.
 */

import { Suspense, useEffect, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { takeSignupEmail } from '../lib/signupEmail'
import { supabaseBrowser } from '../lib/supabaseBrowser'
import { invalidateSession } from '../lib/useSession'

const GOOGLE_ON = process.env.NEXT_PUBLIC_AUTH_GOOGLE === '1'
const MAGIC_ON = process.env.NEXT_PUBLIC_AUTH_MAGIC_LINK === '1'

type Mode = 'signin' | 'signup'
type Notice = { kind: 'error' | 'info'; text: string } | null

function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

/** What the account is for. Every line is something that exists today. */
const REASONS: { k: string; v: string }[] = [
  { k: 'Lab', v: 'Three theories a day, replayed over 111,475 real matches against Pinnacle’s close.' },
  { k: 'Wallet', v: 'Three traders a day, every fill rebuilt into completed round trips.' },
  { k: 'Watchlist', v: 'The fixtures you star, kept.' },
]

function LoginPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const next = safeNext(params.get('next'))
  const prefilled = params.get('email')

  const [mode, setMode] = useState<Mode>(params.get('mode') === 'signup' ? 'signup' : 'signin')
  // Prefilled by /welcome with the address the payment was made on. Using a
  // different one here puts the subscription on the wrong account, so the field
  // is filled rather than left for the reader to remember. It arrives through
  // sessionStorage (lib/signupEmail.ts); `?email=` still works for old links.
  const [email, setEmail] = useState(prefilled ?? '')
  useEffect(() => {
    if (prefilled) return
    const handed = takeSignupEmail()
    if (handed) setEmail((cur) => cur || handed)
  }, [prefilled])
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice>(
    params.get('error') ? { kind: 'error', text: params.get('error')! } : null
  )

  async function withPassword(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setNotice(null)
    const supabase = supabaseBrowser()

    try {
      if (mode === 'signin') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
        // The header's answer to "who is signed in" is cached for the life of
        // the page's JS (useSession), and a client-side push keeps that JS.
        // Without this the next page reads the signed-OUT answer from before
        // the sign-in and offers "Log in" to someone who just did.
        invalidateSession()
        router.push(next)
        router.refresh()
        return
      }

      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
        },
      })
      if (error) throw error

      if (data.session) {
        invalidateSession()
        router.push(next)
        router.refresh()
      } else if (data.user && (data.user.identities?.length ?? 0) === 0) {
        // 🔑 Supabase answers a sign-up for an address that ALREADY has an
        //    account with an obfuscated user carrying no identities, so this
        //    form cannot be used to find out who is registered. The page read
        //    that as success and said "Account created", which is the one
        //    thing it must not say. This keeps Supabase's silence — it does
        //    not confirm the address is taken — and still tells someone who
        //    is stuck what to do next.
        setMode('signin')
        setPassword('')
        setNotice({
          kind: 'info',
          text: `If ${email} is new, its confirmation link is on the way. If it already has an account, no second one was made — sign in below, or use "Forgot your password?".`,
        })
      } else {
        setNotice({
          kind: 'info',
          text: 'Account created. Check your email for the confirmation link — you can sign in once you have clicked it.',
        })
      }
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Something went wrong.' })
    } finally {
      setBusy(false)
    }
  }

  /** A link that signs you in once and lands on /account/password to set a new
   *  one. Worded so it says nothing about whether the address has an account. */
  async function resetPassword() {
    if (!email) {
      setNotice({ kind: 'error', text: 'Enter your email first.' })
      return
    }
    setBusy(true)
    const supabase = supabaseBrowser()
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=%2Faccount%2Fpassword`,
    })
    setBusy(false)
    setNotice(
      error
        ? { kind: 'error', text: error.message }
        : {
            kind: 'info',
            text: `If ${email} has an account, a link to set a new password is on its way. It works once, and not for long.`,
          }
    )
  }

  async function withGoogle() {
    setBusy(true)
    const supabase = supabaseBrowser()
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
    })
    if (error) {
      setNotice({ kind: 'error', text: error.message })
      setBusy(false)
    }
  }

  async function withMagicLink() {
    if (!email) {
      setNotice({ kind: 'error', text: 'Enter your email first.' })
      return
    }
    setBusy(true)
    setNotice(null)
    const supabase = supabaseBrowser()
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
    })
    setBusy(false)
    setNotice(
      error
        ? { kind: 'error', text: error.message }
        : { kind: 'info', text: `Link sent to ${email}. It signs you in on this device.` }
    )
  }

  return (
    <div className="lg-page">
      {/* Card first in the DOM: on a phone this is what the visit is for. */}
      <div className="lg-card">
        <div className="lg-modes" role="tablist" aria-label="Sign in or create an account">
          <button
            role="tab"
            aria-selected={mode === 'signin'}
            className={mode === 'signin' ? 'is-on' : ''}
            onClick={() => { setMode('signin'); setNotice(null) }}
          >
            Sign in
          </button>
          <button
            role="tab"
            aria-selected={mode === 'signup'}
            className={mode === 'signup' ? 'is-on' : ''}
            onClick={() => { setMode('signup'); setNotice(null) }}
          >
            Create account
          </button>
        </div>

        {prefilled && (
          <div className="lg-prefilled">
            Use <strong>{prefilled}</strong> — it is the address your subscription is
            held against.
          </div>
        )}

        {GOOGLE_ON && (
          <>
            <button className="np-btn lg-wide" onClick={withGoogle} disabled={busy}>
              Continue with Google
            </button>
            <div className="lg-or"><span>or</span></div>
          </>
        )}

        <form onSubmit={withPassword} className="lg-form">
          <label className="lg-label" htmlFor="np-email">Email</label>
          <input
            id="np-email"
            className="tp-input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />

          <label className="lg-label" htmlFor="np-password">Password</label>
          <input
            id="np-password"
            className="tp-input"
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === 'signup' ? 'At least 8 characters' : ''}
          />

          <button className="tp-go lg-wide" type="submit" disabled={busy}>
            {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        {mode === 'signin' && (
          <button className="lg-link" onClick={resetPassword} disabled={busy} type="button">
            Forgot your password?
          </button>
        )}

        {MAGIC_ON && (
          <button className="lg-link" onClick={withMagicLink} disabled={busy}>
            Email me a sign-in link instead
          </button>
        )}

        {notice && (
          <div className={`lg-notice${notice.kind === 'error' ? ' is-error' : ''}`}>
            {notice.text}
          </div>
        )}

        <p className="lg-foot">
          {mode === 'signup' ? (
            'No card. The free plan has no expiry and no trial to forget about.'
          ) : (
            <>
              An account changes nothing about what this site claims. The agent is
              still paper and still says so on{' '}
              <Link href="/agent">its own record</Link>.
            </>
          )}
        </p>
      </div>

      {/* The reason. Second in the DOM, first on a wide screen. */}
      <div className="lg-why">
        <span className="tp-eyebrow">FREE ACCOUNT</span>
        <h1 className="lg-h1">
          {mode === 'signup' ? 'It takes an email and a password.' : 'Welcome back.'}
        </h1>
        <p className="lg-sub">
          The boards, Dropping odds, the Game Center and the agent’s record never needed
          one and never will. An account is for the two tools that do real work.
        </p>

        <dl className="lg-reasons">
          {REASONS.map((r) => (
            <div key={r.k} className="lg-reason">
              <dt>{r.k}</dt>
              <dd>{r.v}</dd>
            </div>
          ))}
        </dl>

        <p className="lg-why-foot">
          Want no limit? <Link href="/pricing">Pro is $19 a month</Link> — and you can
          pay without making an account first.
        </p>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <AppShell>
      {/* `useSearchParams` makes this page client-rendered by definition, and
          Next refuses to prerender it without a boundary to fall back to. It has
          nothing to gain from static HTML — it is about one person. */}
      <Suspense fallback={<div className="lg-page" />}>
        <LoginPageInner />
      </Suspense>
    </AppShell>
  )
}
