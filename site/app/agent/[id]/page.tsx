'use client'

/** /agent/<id> — one of the signed-in user's agents: its record, its curve,
 *  its bets, and the controls that work on it. Someone else's id and a missing
 *  one read the same ("not found"), because the API answers both with a 404. */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { useSession } from '../../lib/useSession'
import type { AgentDetail } from '../../lib/agents'
import { AgentView } from '../AgentView'

type Load =
  | { state: 'loading' }
  | { state: 'signed_out' }
  | { state: 'missing' }
  | { state: 'error'; message: string }
  | { state: 'ok'; detail: AgentDetail }

export default function AgentPage({ params }: { params: { id: string } }) {
  const { me } = useSession()
  const router = useRouter()
  const [data, setData] = useState<Load>({ state: 'loading' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(params.id)}`, { cache: 'no-store' })
      if (r.status === 401) return setData({ state: 'signed_out' })
      if (r.status === 404) return setData({ state: 'missing' })
      const d = await r.json()
      if (!d.ok) return setData({ state: 'error', message: d.error ?? 'Could not load the agent.' })
      setData({ state: 'ok', detail: { agent: d.agent, trades: d.trades, curve: d.curve } })
    } catch {
      setData({ state: 'error', message: 'Network error — the agent did not load.' })
    }
  }, [params.id])

  useEffect(() => {
    if (!me) return
    if (!me.user) {
      setData({ state: 'signed_out' })
      return
    }
    load()
  }, [me, load])

  async function act(method: 'PATCH' | 'DELETE', body?: object): Promise<boolean> {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(params.id)}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      const d = await r.json().catch(() => ({ ok: false, error: 'Unexpected response.' }))
      if (!d.ok) {
        setError(d.error)
        return false
      }
      return true
    } catch {
      setError('Network error — nothing changed.')
      return false
    } finally {
      setBusy(false)
    }
  }

  const name = data.state === 'ok' ? data.detail.agent.name : ''

  return (
    <AppShell>
      <div className="gc-main">
        {data.state === 'ok' ? (
          <AgentView
            detail={data.detail}
            actions={{
              busy,
              error,
              onRun: async () => {
                if (await act('PATCH', { run_status: 'running' })) load()
              },
              onPause: async () => {
                if (await act('PATCH', { run_status: 'paused' })) load()
              },
              onArchive: async () => {
                if (!window.confirm(`Archive "${name}"? It stops running; its record is kept.`)) return
                if (await act('DELETE')) router.push('/agent')
              },
            }}
          />
        ) : (
          <>
            <Link href="/agent" className="ag-back">← Your agents</Link>
            <div className="sc-head">
              {data.state === 'loading' && <div className="np-empty">Loading the agent…</div>}
              {data.state === 'missing' && (
                <div className="np-empty">No agent of yours has that address.</div>
              )}
              {data.state === 'signed_out' && (
                <div className="ag-cta-row">
                  <Link
                    href={`/login?next=${encodeURIComponent(`/agent/${params.id}`)}`}
                    className="np-btn np-btn-primary"
                  >
                    Sign in to see this agent →
                  </Link>
                </div>
              )}
              {data.state === 'error' && (
                <div className="np-note sc-error">
                  <strong>Could not load the agent.</strong> {data.message}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
