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
 *     usable until a link is clicked. With it OFF, a session comes back and
 *     the user is in. Both are handled; the form says which happened rather
 *     than showing a spinner that never resolves.
 */

import { Suspense, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { supabaseBrowser } from '../lib/supabaseBrowser'

const GOOGLE_ON = process.env.NEXT_PUBLIC_AUTH_GOOGLE === '1'
const MAGIC_ON = process.env.NEXT_PUBLIC_AUTH_MAGIC_LINK === '1'

type Mode = 'signin' | 'signup'
type Notice = { kind: 'error' | 'info'; text: string } | null

function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

function LoginPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const next = safeNext(params.get('next'))

  const [mode, setMode] = useState<Mode>(
    params.get('mode') === 'signup' ? 'signup' : 'signin'
  )
  // Prefilled by /welcome with the address the payment was made on. Using a
  // different one here puts the subscription on the wrong account, so the
  // field is filled rather than left for the reader to remember.
  const [email, setEmail] = useState(params.get('email') ?? '')
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
        router.push(next)
        router.refresh()
        return
      }

      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
      })
      if (error) throw error

      if (data.session) {
        router.push(next)
        router.refresh()
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
    <AppShell>
      <div className="np-auth">
        <div className="np-auth-card">
          <h1 className="np-auth-title">
            {mode === 'signin' ? 'Sign in' : 'Create an account'}
          </h1>
          <p className="np-auth-sub">
            {params.get('email')
              ? 'Use this address — it is the one the subscription is held against.'
              : mode === 'signin'
                ? 'Scout, Agent and the Game Center never needed one. This is for the Lab and the Wallet.'
                : 'Free: three Lab tests and three wallet reads a day. No card.'}
          </p>

          {GOOGLE_ON && (
            <>
              <button className="np-btn np-auth-wide" onClick={withGoogle} disabled={busy}>
                Continue with Google
              </button>
              <div className="np-auth-or"><span>or</span></div>
            </>
          )}

          <form onSubmit={withPassword} className="np-auth-form">
            <label className="np-auth-label" htmlFor="np-email">Email</label>
            <input
              id="np-email"
              className="np-input"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />

            <label className="np-auth-label" htmlFor="np-password">Password</label>
            <input
              id="np-password"
              className="np-input"
              type="password"
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'signup' ? 'At least 8 characters' : ''}
            />

            <button className="np-btn np-btn-primary np-auth-wide" type="submit" disabled={busy}>
              {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
            </button>
          </form>

          {MAGIC_ON && (
            <button className="np-auth-link" onClick={withMagicLink} disabled={busy}>
              Email me a sign-in link instead
            </button>
          )}

          {notice && (
            <div className={`np-auth-notice ${notice.kind === 'error' ? 'is-error' : ''}`}>
              {notice.text}
            </div>
          )}

          <div className="np-auth-switch">
            {mode === 'signin' ? (
              <>
                No account?{' '}
                <button onClick={() => { setMode('signup'); setNotice(null) }}>Create one</button>
              </>
            ) : (
              <>
                Already have one?{' '}
                <button onClick={() => { setMode('signin'); setNotice(null) }}>Sign in</button>
              </>
            )}
          </div>

          <p className="np-auth-foot">
            An account changes nothing about what the site claims. The agent is still
            paper and still says so on <Link href="/agent">its own tab</Link>.
          </p>
        </div>
      </div>
    </AppShell>
  )
}

/** `useSearchParams` makes this page client-rendered by definition, and Next
 *  refuses to prerender it without a boundary to fall back to. Neither page
 *  has anything to gain from static HTML — both are about one specific
 *  person — so the boundary is the whole answer rather than a workaround. */
export default function LoginPage() {
  return (
    <Suspense fallback={<div className="np-auth"><div className="np-auth-card" /></div>}>
      <LoginPageInner />
    </Suspense>
  )
}
