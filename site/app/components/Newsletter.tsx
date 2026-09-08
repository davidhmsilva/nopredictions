'use client'

/** The newsletter form.
 *
 *  ⚠️ It says, before you type anything, that nothing is being sent yet. That
 *     sentence is not modesty — this site removed a newsletter form that set
 *     local state, showed a tick, and sent nowhere. The address here is stored
 *     for real; what does not exist yet is the sending. Saying which is which
 *     is the whole difference between the two versions.
 */

import { useState, type FormEvent } from 'react'

export function Newsletter({ source = 'insights' }: { source?: string }) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/api/newsletter', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, source }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.error ?? 'Could not save that.')
      setDone(body.message)
      setEmail('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save that.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="np-news">
      <div className="np-news-copy">
        <h2>Get these by email</h2>
        <p>
          What we measured, what it said, and what it did not. Including the
          things that did not work — those are most of them.
        </p>
      </div>

      {done ? (
        <div className="np-news-done" role="status">{done}</div>
      ) : (
        <form className="np-news-form" onSubmit={submit}>
          <input
            className="np-input"
            type="email"
            required
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            placeholder="you@example.com"
            aria-label="Email address"
            disabled={busy}
          />
          <button className="np-btn np-btn-primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Subscribe'}
          </button>
        </form>
      )}

      {error && <p className="np-news-error">{error}</p>}

      <p className="np-news-note">
        Your address is stored and nothing else is done with it.{' '}
        <strong>No issue has been sent yet</strong> — there is no mailer wired up,
        and this page will not pretend otherwise. When the first one goes out it
        will say it is the first.
      </p>
    </section>
  )
}
