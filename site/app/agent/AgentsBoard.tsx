'use client'

/** The list of a user's agents, drawn as a Scout board: the running ones get a
 *  card, every one gets a row. Presentational — the page fetches, this draws —
 *  so the same view can be rendered from any source of AgentSummary rows. */

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import type { AgentLimits, AgentSummary } from '../lib/agents'
import { Sparkline, StatusBadge, ago, pct, settledOf, sinceText, tone, units } from './parts'

type SortKey = 'status' | 'pl' | 'yield' | 'bets' | 'recent'

// P&L first, by the user's call: what is making money is what they open this
// page to see. Ties (no settled bets) sit between the winners and the losers.
const SORTS: { id: SortKey; label: string }[] = [
  { id: 'pl', label: 'Best P&L first' },
  { id: 'status', label: 'Running first' },
  { id: 'yield', label: 'Yield' },
  { id: 'bets', label: 'Most bets' },
  { id: 'recent', label: 'Last bet' },
]

const byLast = (a: AgentSummary, b: AgentSummary) =>
  (b.last_bet_at ? Date.parse(b.last_bet_at) : 0) - (a.last_bet_at ? Date.parse(a.last_bet_at) : 0)

function sorted(agents: AgentSummary[], key: SortKey): AgentSummary[] {
  const xs = [...agents]
  switch (key) {
    case 'pl':
      return xs.sort((a, b) => b.pl_units - a.pl_units)
    case 'yield':
      return xs.sort((a, b) => (b.yield_pct ?? -Infinity) - (a.yield_pct ?? -Infinity))
    case 'bets':
      return xs.sort((a, b) => settledOf(b) - settledOf(a))
    case 'recent':
      return xs.sort(byLast)
    default:
      // The server already orders running first, then by the latest bet.
      return xs
  }
}

function AgentCard({ a }: { a: AgentSummary }) {
  const settled = settledOf(a)
  return (
    <Link href={`/agent/${a.id}`} className="ag-card">
      <div className="ag-card-top">
        <span className="ag-kind">{sinceText(a)}</span>
        <StatusBadge a={a} />
      </div>
      <div className="ag-card-name">{a.name}</div>
      <Sparkline values={a.spark} />
      <div className="ag-tiles">
        <span className="ag-tile">
          <em>Bets</em>
          <b className="np-num">{settled}</b>
        </span>
        <span className="ag-tile">
          <em>W–L</em>
          <b className="np-num">{settled ? `${a.wins}–${a.losses}` : '—'}</b>
        </span>
        <span className="ag-tile">
          <em>P&amp;L</em>
          <b className={`np-num ${tone(settled ? a.pl_units : null)}`}>{settled ? units(a.pl_units) : '—'}</b>
        </span>
        <span className="ag-tile">
          <em>Yield</em>
          <b className={`np-num ${tone(a.yield_pct)}`}>{a.yield_pct != null ? pct(a.yield_pct) : '—'}</b>
        </span>
      </div>
      <div className="ag-card-foot">
        {a.n_open > 0 && <span className="np-num">{a.n_open} open</span>}
        <span>Last bet {ago(a.last_bet_at)}</span>
        <span className="sc-report">
          View agent <span aria-hidden="true">→</span>
        </span>
      </div>
    </Link>
  )
}

export function AgentsBoard({ agents, limits }: { agents: AgentSummary[]; limits: AgentLimits }) {
  const router = useRouter()
  const [sort, setSort] = useState<SortKey>('pl')
  const [query, setQuery] = useState('')

  const running = agents
    .filter((a) => a.run_status === 'running')
    .sort((a, b) => b.pl_units - a.pl_units)
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const hit = q
      ? agents.filter((a) => `${a.name} ${a.theory ?? ''} ${a.interpretation ?? ''}`.toLowerCase().includes(q))
      : agents
    return sorted(hit, sort)
  }, [agents, sort, query])

  return (
    <>
      <div className="sc-head ag-head">
        <div>
          <p className="sc-eyebrow">AGENTS · Paper trading</p>
          <h1 className="sc-h1">Your agents</h1>
          <p className="sc-h1-sub">
            Each one trades today&apos;s Polymarket boards on paper — 1 unit at the real ask, logged
            before the event and settled on the result. Nothing here places an order. Run anything,
            watch what it does, and keep what earns its place.
          </p>
        </div>
        <div className="ag-head-cta">
          <Link href="/lab" className="np-btn np-btn-primary">New agent →</Link>
          <span className="ag-limits np-num">
            {limits.unlimited
              ? `${running.length} running · no limits`
              : `${limits.used_running} of ${limits.running} running · ${limits.used_saved} of ${limits.saved} kept`}
          </span>
        </div>
      </div>

      {agents.length === 0 ? (
        <div className="ag-empty">
          <b>No agents yet.</b> Write a theory in the Lab — &ldquo;back home favourites in La
          Liga&rdquo;, &ldquo;overs when both teams scored last time&rdquo; — see how it would have
          done, and save it as an agent. Switch it on and it starts paper-trading the next games
          that fit.
          <div>
            <Link href="/lab" className="np-btn np-btn-primary">Open the Lab →</Link>
          </div>
        </div>
      ) : (
        <>
          {running.length > 0 && (
            <div className="ag-grid">
              {running.map((a) => (
                <AgentCard key={a.id} a={a} />
              ))}
            </div>
          )}

          <div className="sc-bar">
            <div className="sc-bar-left">
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
                placeholder="Filter your agents…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Filter agents"
              />
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="np-empty">No agent matches that filter.</div>
          ) : (
            <div className="sc-table-wrap">
              <table className="sc-table">
                <thead>
                  <tr>
                    <th className="sc-c-n">#</th>
                    <th>Agent</th>
                    <th className="ag-c-status">Status</th>
                    <th className="ag-c-num ag-hide-m" title="Settled bets">Bets</th>
                    <th className="ag-c-num ag-hide-m">W–L</th>
                    <th className="ag-c-num" title="Profit or loss in units, 1 unit per bet">P&amp;L</th>
                    <th className="ag-c-num ag-hide-m">Yield</th>
                    <th className="ag-c-num ag-hide-m" title="Average closing-line value, where it was measured">CLV</th>
                    <th className="ag-c-when ag-hide-m">Last bet</th>
                    <th className="sc-c-go" />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((a, i) => {
                    const settled = settledOf(a)
                    const sub = a.interpretation && a.interpretation !== a.name ? a.interpretation : sinceText(a)
                    return (
                      <tr key={a.id} className="is-clickable" onClick={() => router.push(`/agent/${a.id}`)}>
                        <td className="sc-c-n np-num">{i + 1}</td>
                        <td>
                          <Link href={`/agent/${a.id}`} className="ag-row-name" onClick={(e) => e.stopPropagation()}>
                            {a.name}
                          </Link>
                          <span className="ag-row-sub">{sub}</span>
                        </td>
                        <td className="ag-c-status"><StatusBadge a={a} /></td>
                        <td className="ag-c-num ag-hide-m np-num">
                          {settled}
                          {a.n_open > 0 && <span className="ag-row-sub">+{a.n_open} open</span>}
                        </td>
                        <td className="ag-c-num ag-hide-m np-num">{settled ? `${a.wins}–${a.losses}` : '—'}</td>
                        <td className={`ag-c-num np-num ${tone(settled ? a.pl_units : null)}`}>
                          {settled ? units(a.pl_units) : '—'}
                        </td>
                        <td className={`ag-c-num ag-hide-m np-num ${tone(a.yield_pct)}`}>
                          {a.yield_pct != null ? pct(a.yield_pct) : '—'}
                        </td>
                        <td className={`ag-c-num ag-hide-m np-num ${tone(a.avg_clv)}`}>
                          {a.avg_clv != null ? pct(a.avg_clv * 100) : '—'}
                        </td>
                        <td className="ag-c-when ag-hide-m">{ago(a.last_bet_at)}</td>
                        <td className="sc-c-go">
                          <Link href={`/agent/${a.id}`} className="sc-go" onClick={(e) => e.stopPropagation()}>
                            View <span aria-hidden="true">→</span>
                          </Link>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  )
}
