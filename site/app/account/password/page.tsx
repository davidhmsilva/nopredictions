'use client'

/** Set a new password.
 *
 *  Reached from the link in a password-reset email: /auth/callback exchanges
 *  its code for a session and sends the browser here, so whoever opens this
 *  page is already signed in — as the person who can read that inbox. Someone
 *  signed in the ordinary way can use it too, which is what an account page
 *  should offer anyway.
 *
 *  A link that has expired or was already spent leaves no session, and this
 *  page says exactly that instead of a form that cannot work.
 */

import { useEffect, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { supabaseBrowser } from '../../lib/supabaseBrowser'
import { invalidateSession } from '../../lib/useSession'

const MIN = 8

type State = 'checking' | 'ready' | 'no_session' | 'done'

export default function NewPasswordPage() {
  const router = useRouter()
  const [state, setState] = useState<State>('checking')
  const [email, setEmail] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    ;(async () => {
      const { data } = await supabaseBrowser().auth.getUser()
      if (!live) return
      setEmail(data.user?.email ?? null)
      setState(data.user ? 'ready' : 'no_session')
    })()
    return () => {
      live = false
    }
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < MIN) return setError(`At least ${MIN} characters.`)
    if (password !== again) return setError('The two do not match.')
    setBusy(true)
    const { error: err } = await supabaseBrowser().auth.updateUser({ password })
    setBusy(false)
    if (err) return setError(err.message)
    invalidateSession()
    setState('done')
    router.refresh()
  }

  return (
    <AppShell>
      <div className="lg-page">
        <div className="lg-card">
          <h1 className="lg-card-h1">Set a new password</h1>

          {state === 'checking' && <p className="lg-sub">Checking your link…</p>}

          {state === 'no_session' && (
            <>
              <p className="lg-sub">
                This link has expired or has already been used. Ask for another one and open the
                newest email.
              </p>
              <Link href="/login" className="np-btn np-btn-primary lg-wide">
                Back to sign in
              </Link>
            </>
          )}

          {state === 'ready' && (
            <>
              <p className="lg-sub">
                {email ? <>For <strong>{email}</strong>. </> : null}
                It replaces the old one straight away, on every device.
              </p>
              <form onSubmit={submit} className="lg-form">
                <label className="lg-label" htmlFor="np-new-password">New password</label>
                <input
                  id="np-new-password"
                  className="tp-input"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={MIN}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={`At least ${MIN} characters`}
                />
                <label className="lg-label" htmlFor="np-new-password-2">Again</label>
                <input
                  id="np-new-password-2"
                  className="tp-input"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={MIN}
                  value={again}
                  onChange={(e) => setAgain(e.target.value)}
                />
                <button className="tp-go lg-wide" type="submit" disabled={busy}>
                  {busy ? 'Saving…' : 'Save the new password'}
                </button>
              </form>
              {error && <div className="lg-notice is-error">{error}</div>}
            </>
          )}

          {state === 'done' && (
            <>
              <div className="lg-notice">Saved. You are signed in with it now.</div>
              <Link href="/account" className="np-btn np-btn-primary lg-wide">
                Go to your account
              </Link>
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}
