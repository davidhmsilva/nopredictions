'use client'

/** A published agent's public page: the owner's view of it, read-only, with
 *  the author's name and Copy where the controls would be. The server normally
 *  sends the agent in the HTML; if the database was too slow for that, it is
 *  asked for from here. */

import Link from 'next/link'
import { useEffect, useState } from 'react'
import type { AgentDetail } from '../../lib/agents'
import { AgentView } from '../../agent/AgentView'
import { CopyButton } from '../../components/CopyButton'
import { relationOf, useMarketplaceMe } from '../../lib/useMarketplaceMe'

type Load = { state: 'loading' } | { state: 'missing' } | { state: 'error' } | { state: 'ok'; detail: AgentDetail }

export function PublicAgent({ id, initial }: { id: number; initial: AgentDetail | null }) {
  const me = useMarketplaceMe()
  const [data, setData] = useState<Load>(initial ? { state: 'ok', detail: initial } : { state: 'loading' })

  useEffect(() => {
    if (initial) return
    fetch(`/api/marketplace/${id}`)
      .then(async (r) => {
        if (r.status === 404) return setData({ state: 'missing' })
        const d = await r.json()
        setData(
          d.ok
            ? {
                state: 'ok',
                detail: {
                  agent: d.agent,
                  trades: d.trades,
                  curve: d.curve,
                  orders: d.orders ?? [],
                  positions: d.positions ?? [],
                  legacy_positions: d.legacy_positions ?? 0,
                },
              }
            : { state: 'error' }
        )
      })
      .catch(() => setData({ state: 'error' }))
  }, [id, initial])

  if (data.state !== 'ok') {
    return (
      <>
        <Link href="/marketplace" className="ag-back">← Marketplace</Link>
        <div className="sc-head">
          {data.state === 'loading' && <div className="np-empty">Loading the agent…</div>}
          {data.state === 'missing' && <div className="np-empty">No published agent has that address.</div>}
          {data.state === 'error' && (
            <div className="np-note sc-error">
              <strong>The agent did not load.</strong> Try again in a minute.
            </div>
          )}
        </div>
      </>
    )
  }

  return (
    <AgentView
      detail={data.detail}
      mode="public"
      aside={<CopyButton id={id} relation={relationOf(me, id)} />}
    />
  )
}
