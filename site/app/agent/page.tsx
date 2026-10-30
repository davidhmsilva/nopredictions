'use client'

/** My agents — the signed-in user's own, and nobody else's.
 *
 *  Private by construction: everything here comes from /api/agents, which reads
 *  on the server with the owner check in the SQL. An agent its owner publishes
 *  also gets a public page on the Marketplace (/marketplace/<id>); this page is
 *  where it is run, paused, published and archived.
 *
 *  Every agent is drawn the same way, wherever it came from (2026-09-19): a
 *  card while it runs, a row always, and a page of its own at /agent/<id>.
 */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { useSession } from '../lib/useSession'
import type { AgentLimits, AgentSummary } from '../lib/agents'
import { AgentsBoard } from './AgentsBoard'

type Load =
  | { state: 'loading' }
  | { state: 'signed_out' }
  | { state: 'error'; message: string }
  | { state: 'ok'; agents: AgentSummary[]; limits: AgentLimits }

export default function AgentsPage() {
  const { me } = useSession()
  const [data, setData] = useState<Load>({ state: 'loading' })

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/agents', { cache: 'no-store' })
      if (r.status === 401) return setData({ state: 'signed_out' })
      const d = await r.json()
      if (!d.ok) return setData({ state: 'error', message: d.error ?? 'Could not load your agents.' })
      setData({ state: 'ok', agents: d.agents, limits: d.limits })
    } catch {
      setData({ state: 'error', message: 'Network error — your agents did not load.' })
    }
  }, [])

  // Ask only once the session is known. A signed-out visit would otherwise fire
  // a request that can only 401 and log it as a console error on every
  // anonymous page view — the noise /api/me was written to avoid.
  useEffect(() => {
    if (!me) return
    if (!me.user) {
      setData({ state: 'signed_out' })
      return
    }
    load()
  }, [me, load])

  return (
    <AppShell>
      <div className="np-wrap">
        {data.state === 'signed_out' ? (
          <div className="sc-head ag-head">
            <div>
              <p className="sc-eyebrow">MY AGENTS · Paper trading</p>
              <h1 className="sc-h1">Your ideas, working while you look away</h1>
              <p className="sc-h1-sub">
                Describe an idea and the AI turns it into an agent. Deploy it and it places practice
                bets on today&apos;s games at the real price — logged before kick-off, settled on the
                result — and you watch its record build. Or copy one from the Marketplace.
              </p>
              <div className="ag-cta-row">
                <Link href="/login?next=%2Fagent" className="np-btn np-btn-primary">Sign in to see yours →</Link>
                <Link href="/create" className="np-btn">Create an agent</Link>
                <Link href="/marketplace" className="np-btn">Browse the Marketplace</Link>
              </div>
            </div>
          </div>
        ) : data.state === 'ok' ? (
          <AgentsBoard agents={data.agents} limits={data.limits} />
        ) : (
          <div className="sc-head">
            <p className="sc-eyebrow">MY AGENTS · Paper trading</p>
            <h1 className="sc-h1">Your agents</h1>
            {data.state === 'loading' ? (
              <div className="np-empty">Loading your agents…</div>
            ) : (
              <div className="np-note sc-error">
                <strong>Could not load your agents.</strong> {data.message}
              </div>
            )}
          </div>
        )}
      </div>
    </AppShell>
  )
}
