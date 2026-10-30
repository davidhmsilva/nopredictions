'use client'

/** Copy a Marketplace agent into your own account and switch it on, in one
 *  press. What the viewer already has decides the label: their own agent says
 *  so, an agent they copied before opens their copy, and anything else copies.
 *
 *  Signed out, the press goes to sign-up and comes back to the same agent —
 *  copying is the most direct way to have an agent running, so it must not end
 *  on a sign-in form with nothing to return to.
 */

import Link from 'next/link'
import { useState } from 'react'

export type Relation = { kind: 'mine' } | { kind: 'copied'; copyId: number } | { kind: 'none' }

export function CopyButton({
  id,
  relation,
  compact = false,
}: {
  id: number
  relation: Relation
  /** On a card: one button, no explanation under it. */
  compact?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ id: number; running: boolean; note: string | null } | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (relation.kind === 'mine') {
    return (
      <Link href={`/agent/${id}`} className="np-btn mk-copy">
        Yours — manage it
      </Link>
    )
  }
  const copyId = done?.id ?? (relation.kind === 'copied' ? relation.copyId : null)
  if (copyId != null) {
    return (
      <div className="mk-copied">
        <Link href={`/agent/${copyId}`} className="np-btn mk-copy">
          Open your copy <span aria-hidden="true">→</span>
        </Link>
        {/* On a card only the bad news is worth the room: it copied, but did not start. */}
        {done && (!compact || !done.running) && (
          <p className="mk-copy-note">
            {done.running
              ? 'Copied and running. It places its own paper bets from now on — this record stays its author’s.'
              : `Copied to your agents, not running yet${done.note ? `: ${done.note}` : '.'}`}
          </p>
        )}
      </div>
    )
  }

  async function copy() {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch(`/api/marketplace/${id}/copy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deploy: true }),
      })
      const d = await r.json().catch(() => ({ ok: false, error: 'Unexpected response.' }))
      if (r.status === 401) {
        window.location.href = `/login?mode=signup&next=${encodeURIComponent(`/marketplace/${id}`)}`
        return
      }
      if (!d.ok) setError(d.error ?? 'Could not copy it.')
      else setDone({ id: d.id, running: Boolean(d.running), note: d.note ?? null })
    } catch {
      setError('Network error — nothing was copied.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mk-copied">
      <button type="button" className="np-btn np-btn-primary mk-copy" disabled={busy} onClick={copy}>
        {busy ? 'Copying…' : 'Copy and run'}
      </button>
      {error && <p className="mk-copy-note is-error">{error}</p>}
      {!compact && !error && (
        <p className="mk-copy-note">
          Your copy runs the same rule in your account and builds its own record. Paper money, real
          prices.
        </p>
      )}
    </div>
  )
}
