'use client'

/** The Marketplace list: every published agent as a card, sortable, with a
 *  Copy button on each. The server renders the list (so it is in the HTML and
 *  a shared link shows it at once); the browser adds what the viewer already
 *  owns or copied, from one extra request. */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import type { AgentSummary } from '../lib/agents'
import { isInplaySpec } from '../lib/inplaySpec'
import { relationOf, useMarketplaceMe } from '../lib/useMarketplaceMe'
import { settledOf } from '../agent/parts'
import { MarketCard } from './MarketCard'

type SortKey = 'pl' | 'bets' | 'copies' | 'new'
type Kind = 'all' | 'prematch' | 'live'

// Profit first, because that is what someone opens a marketplace of agents to
// see — with the sample size on every card and "early record" under 200 bets,
// so a lucky fortnight cannot pass for a result.
const SORTS: { id: SortKey; label: string }[] = [
  { id: 'pl', label: 'Best P&L' },
  { id: 'bets', label: 'Most bets' },
  { id: 'copies', label: 'Most copied' },
  { id: 'new', label: 'Newest' },
]

const KINDS: { id: Kind; label: string }[] = [
  { id: 'all', label: 'All agents' },
  { id: 'prematch', label: 'Before kick-off' },
  { id: 'live', label: 'Live rules' },
]

function sorted(xs: AgentSummary[], key: SortKey): AgentSummary[] {
  const out = [...xs]
  switch (key) {
    case 'bets':
      return out.sort((a, b) => settledOf(b) - settledOf(a))
    case 'copies':
      return out.sort((a, b) => b.copies - a.copies)
    case 'new':
      return out.sort(
        (a, b) =>
          Date.parse(b.published_at ?? b.created_at) - Date.parse(a.published_at ?? a.created_at)
      )
    default:
      return out.sort((a, b) => b.pl_units - a.pl_units)
  }
}

export function MarketplaceView({ initial }: { initial: AgentSummary[] | null }) {
  const [agents, setAgents] = useState<AgentSummary[] | null>(initial)
  const [failed, setFailed] = useState(false)
  const [sort, setSort] = useState<SortKey>('pl')
  const [kind, setKind] = useState<Kind>('all')
  const [query, setQuery] = useState('')
  const me = useMarketplaceMe()

  // The server render failed or was skipped: ask for the list from here.
  useEffect(() => {
    if (initial) return
    fetch('/api/marketplace')
      .then((r) => r.json())
      .then((d) => (d.ok ? setAgents(d.agents) : setFailed(true)))
      .catch(() => setFailed(true))
  }, [initial])

  const shown = useMemo(() => {
    if (!agents) return []
    const q = query.trim().toLowerCase()
    const hit = agents.filter((a) => {
      const live = isInplaySpec(a.spec)
      if (kind === 'live' && !live) return false
      if (kind === 'prematch' && live) return false
      if (!q) return true
      return `${a.name} ${a.theory ?? ''} ${a.interpretation ?? ''} ${a.author}`.toLowerCase().includes(q)
    })
    return sorted(hit, sort)
  }, [agents, sort, kind, query])

  return (
    <>
      <div className="sc-head ag-head">
        <div>
          <p className="sc-eyebrow">MARKETPLACE · Agents anyone can copy</p>
          <h1 className="sc-h1">Copy an agent that is already running</h1>
          <p className="sc-h1-sub">
            Every agent here was built by someone from their own idea and published with its strategy
            and its whole record. Copy one and it runs the same rule in your account, placing its own
            paper bets from today. Practice money, real prices.
          </p>
        </div>
        <div className="ag-head-cta">
          <Link href="/create" className="np-btn np-btn-primary">Build your own →</Link>
        </div>
      </div>

      {agents === null && !failed && <div className="np-empty">Loading the Marketplace…</div>}
      {failed && (
        <div className="np-note sc-error">
          <strong>The Marketplace did not load.</strong> Try again in a minute.
        </div>
      )}

      {agents !== null && agents.length === 0 && (
        <div className="ag-empty">
          <b>No agents published yet.</b> The Marketplace fills as people publish the agents they
          build. Build one from your own idea, let it run, and publish it from its page — yours could
          be the first one here.
          <div>
            <Link href="/create" className="np-btn np-btn-primary">Build an agent →</Link>
          </div>
        </div>
      )}

      {agents !== null && agents.length > 0 && (
        <>
          <div className="sc-bar">
            <div className="sc-bar-left">
              <div className="mk-kinds" role="group" aria-label="Kind of agent">
                {KINDS.map((k) => (
                  <button
                    key={k.id}
                    type="button"
                    className={`tp-chip${kind === k.id ? ' is-on' : ''}`}
                    aria-pressed={kind === k.id}
                    onClick={() => setKind(k.id)}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
              <span className="sc-count np-num">
                {shown.length === agents.length ? `${agents.length} agents` : `${shown.length} of ${agents.length}`}
              </span>
            </div>
            <div className="sc-bar-right">
              <label className="sc-sort">
                <span className="sc-sort-key">Sort</span>
                <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort agents">
                  {SORTS.map((s) => (
                    <option key={s.id} value={s.id}>{s.label}</option>
                  ))}
                </select>
              </label>
              <input
                className="sc-search"
                placeholder="Search agents…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search agents"
              />
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="np-empty">No agent matches that.</div>
          ) : (
            <div className="mk-grid">
              {shown.map((a) => (
                <MarketCard key={a.id} a={a} relation={relationOf(me, a.id)} />
              ))}
            </div>
          )}

          <p className="ag-note mk-honest">
            A record under 200 settled bets is mostly the draw — a good month can look like a pattern
            for a long time. Every agent here paper-trades: nothing on this page is a tip, and no real
            money is bet.
          </p>
        </>
      )}
    </>
  )
}
