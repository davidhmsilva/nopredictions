'use client'

/** Agents — the signed-in user's own, and nobody else's.
 *
 *  Private by construction: everything here comes from /api/agents, which reads
 *  on the server with the owner check in the SQL. There is no public record any
 *  more — the operator's hand-written pressure arms are HIS agents, shown to
 *  him here and nowhere else (2026-09-11). A public leaderboard of agents users
 *  choose to publish is the next step, and until it exists this page offers no
 *  publish button: a control that leads nowhere is the kind of thing this site
 *  has already deleted twice.
 *
 *  Two kinds on one page:
 *    FROM THE LAB  a saved theory. Run / pause / archive here;
 *                  agent/lab_strategy_runner.py does the paper trading.
 *    HAND-WRITTEN  a daemon on the operator's machine. Its full record,
 *                  read-only — it is switched on that machine, not here.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { AgentSection } from '../components/AgentSection'
import { AgentStats } from '../components/AgentStats'
import { useSession } from '../lib/useSession'
import type { PaperTrade, PressureTrade, Strategy } from '../lib/supabase'
import type { AgentLimits, AgentSummary } from '../lib/agents'

// ── formatting ──────────────────────────────────────────────────────────────

const signed = (n: number, digits = 2) => `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

function toStrategy(a: AgentSummary): Strategy {
  const settled = a.wins + a.losses
  return {
    id: a.id,
    hypothesis_id: null,
    name: a.name,
    rules: null,
    promoted_at: a.promoted_at,
    retired_at: null,
    retirement_reason: null,
    parent_strategy_id: a.parent_strategy_id,
    total_bets: settled,
    wins: a.wins,
    losses: a.losses,
    win_rate: settled > 0 ? a.wins / settled : 0,
    avg_clv: a.avg_clv,
    total_pnl: a.pl_units,
    yield_pct: a.yield_pct,
  }
}

const STATUS_BADGE: Record<AgentSummary['run_status'], { label: string; cls: string }> = {
  running: { label: 'RUNNING · PAPER', cls: 'is-good' },
  paused: { label: 'PAUSED', cls: '' },
  draft: { label: 'SAVED · NOT RUNNING', cls: '' },
}

// ── a Lab agent ─────────────────────────────────────────────────────────────

function LabAgentCard({
  a,
  trades,
  busy,
  error,
  onRun,
  onPause,
  onArchive,
}: {
  a: AgentSummary
  trades: PaperTrade[]
  busy: boolean
  error: string | null
  onRun: () => void
  onPause: () => void
  onArchive: () => void
}) {
  const bt = a.backtest
  const settled = a.wins + a.losses
  const badge = STATUS_BADGE[a.run_status]
  return (
    <article className="ag-card">
      <header className="ag-card-head">
        <h3>{a.name}</h3>
        <span className={`np-badge ${badge.cls}`}>{badge.label}</span>
      </header>
      {a.interpretation && a.interpretation !== a.name && (
        <p className="ag-card-interp">{a.interpretation}</p>
      )}

      <dl className="ag-card-rows">
        <div>
          <dt>Backtest</dt>
          <dd>
            {bt && bt.stats.n > 0 ? (
              <>
                <span className="np-num">{bt.stats.n.toLocaleString('en-US')}</span> bets ·
                yield <span className="np-num">{signed(bt.stats.yieldPct)}%</span> ±{' '}
                <span className="np-num">{bt.stats.ci95Pct.toFixed(2)}</span>
                {bt.stats.clvPct != null && (
                  <> · CLV <span className="np-num">{signed(bt.stats.clvPct)}%</span></>
                )}{' '}
                · <strong>{bt.verdict.label}</strong>
              </>
            ) : (
              'No historical matches fit it.'
            )}
          </dd>
        </div>
        <div>
          <dt>Paper record</dt>
          <dd>
            {a.n_trades === 0 ? (
              a.run_status === 'running' ? 'Running — no qualifying fixture yet.' : 'Not run yet.'
            ) : (
              <>
                <span className="np-num">{settled}</span> settled ({a.wins}-{a.losses})
                {a.n_open > 0 && <> · {a.n_open} open</>} ·{' '}
                <span className={`np-num ${a.pl_units >= 0 ? 'is-pos' : 'is-neg'}`}>
                  {signed(a.pl_units)}u
                </span>
                {a.yield_pct != null && <> · yield <span className="np-num">{signed(a.yield_pct)}%</span></>}
              </>
            )}
          </dd>
        </div>
      </dl>

      {a.run_blocker && <p className="np-note ag-card-blocker">{a.run_blocker}</p>}
      {settled > 0 && settled < 200 && (
        <p className="ag-card-small">
          {settled} of the 200 settled bets any verdict needs. Until then this is a record, not a result.
        </p>
      )}

      {trades.length > 0 && (
        <ul className="ag-card-trades">
          {trades.slice(0, 5).map((t) => (
            <li key={t.id}>
              <span className="np-num">{shortDate(t.placed_at)}</span>
              <span className="ag-card-trade-what">{t.outcome}</span>
              <span className="np-num">{Number(t.entry_odds).toFixed(2)}</span>
              <span className={t.result === 'won' ? 'is-pos' : t.result === 'lost' ? 'is-neg' : ''}>
                {t.result ? t.result.toUpperCase() : 'OPEN'}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="ag-card-actions">
        {a.run_status === 'running' ? (
          <button className="np-btn" disabled={busy} onClick={onPause}>Pause</button>
        ) : (
          <button
            className="np-btn np-btn-primary"
            disabled={busy || Boolean(a.run_blocker)}
            onClick={onRun}
            title={a.run_blocker ?? undefined}
          >
            Run it on today&apos;s boards
          </button>
        )}
        <button className="np-btn np-btn-ghost" disabled={busy} onClick={onArchive}>Archive</button>
      </div>
      {error && <p className="ag-card-error">{error}</p>}
    </article>
  )
}

// ── page ────────────────────────────────────────────────────────────────────

type Load =
  | { state: 'loading' }
  | { state: 'signed_out' }
  | { state: 'error'; message: string }
  | {
      state: 'ok'
      agents: AgentSummary[]
      limits: AgentLimits
      trades: PaperTrade[]
      pressure: Map<number, PressureTrade>
    }

export default function AgentsPage() {
  const { me } = useSession()
  const [data, setData] = useState<Load>({ state: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
  const [cardError, setCardError] = useState<{ id: number; msg: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const [a, t] = await Promise.all([
        fetch('/api/agents', { cache: 'no-store' }),
        fetch('/api/agents/trades', { cache: 'no-store' }),
      ])
      if (a.status === 401) return setData({ state: 'signed_out' })
      const ad = await a.json()
      const td = t.ok ? await t.json() : { trades: [], pressure: [] }
      if (!ad.ok) return setData({ state: 'error', message: ad.error ?? 'Could not load your agents.' })
      setData({
        state: 'ok',
        agents: ad.agents,
        limits: ad.limits,
        trades: td.trades ?? [],
        pressure: new Map((td.pressure ?? []).map((p: PressureTrade) => [p.paper_trade_id, p])),
      })
    } catch {
      setData({ state: 'error', message: 'Network error — your agents did not load.' })
    }
  }, [])

  // Ask only once the session is known. A signed-out visit would otherwise fire
  // two requests that can only 401 and log both as console errors on every
  // anonymous page view — the noise /api/me was written to avoid.
  useEffect(() => {
    if (!me) return
    if (!me.user) {
      setData({ state: 'signed_out' })
      return
    }
    load()
  }, [me, load])

  async function act(id: number, method: 'PATCH' | 'DELETE', body?: object) {
    setBusy(id)
    setCardError(null)
    try {
      const r = await fetch(`/api/agents/${id}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      const d = await r.json().catch(() => ({ ok: false, error: 'Unexpected response.' }))
      if (!d.ok) setCardError({ id, msg: d.error })
      await load()
    } catch {
      setCardError({ id, msg: 'Network error — nothing changed.' })
    } finally {
      setBusy(null)
    }
  }

  const ok = data.state === 'ok' ? data : null
  const lab = useMemo(() => ok?.agents.filter((a) => a.source === 'lab') ?? [], [ok])
  const handWritten = useMemo(() => ok?.agents.filter((a) => a.source === 'agent') ?? [], [ok])
  const handIds = useMemo(() => new Set(handWritten.map((a) => a.id)), [handWritten])
  const handTrades = useMemo(
    () => ok?.trades.filter((t) => handIds.has(t.strategy_id)) ?? [],
    [ok, handIds],
  )

  if (data.state === 'signed_out' || (data.state === 'loading' && me && !me.user)) {
    return (
      <AppShell>
        <div className="ag-empty">
          <header className="ag-empty-head">
            <span className="ag-empty-eyebrow">AGENTS</span>
            <h1>Your agents live here.</h1>
            <p>
              An agent is a theory that keeps working after you stop looking at it. You
              write it in the Lab, see what it would have made over 111,475 games, save it,
              and switch it on — from then on it paper-trades today&apos;s Polymarket boards
              and keeps an honest score.
            </p>
            <div className="ag-empty-cta">
              <Link href="/login?next=%2Fagent" className="np-btn np-btn-primary">
                Sign in to see yours →
              </Link>
              <Link href="/lab" className="np-btn">Start in the Lab</Link>
            </div>
            <p className="ag-empty-note">A free account keeps 5 agents and runs 1. No card.</p>
          </header>
          <div className="np-note ag-empty-honest">
            <strong>Paper, not money.</strong> An agent buys at the real Polymarket ask,
            with a simulated 1-unit stake, logged before the match starts and settled on the
            real result. Nothing here places an order. And a record under 200 settled bets is
            a record, not a result — the page says so on every agent.
          </div>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="ag-mine">
        <header className="ag-mine-head">
          <span className="ag-empty-eyebrow">AGENTS</span>
          <h1>Your agents</h1>
          {ok && (
            <p className="ag-mine-limits">
              {ok.limits.unlimited
                ? `Owner account — no limits. ${ok.limits.used_running} running, ${ok.limits.used_saved} saved from the Lab.`
                : `${ok.limits.used_running} of ${ok.limits.running} running · ${ok.limits.used_saved} of ${ok.limits.saved} saved.`}
            </p>
          )}
        </header>

        {data.state === 'loading' && <p className="ag-mine-status">Loading your agents…</p>}
        {data.state === 'error' && <p className="ag-mine-status is-neg">{data.message}</p>}

        {ok && (
          <>
            <section className="ag-mine-section">
              <div className="tp-section-head">
                <h2>From the Lab</h2>
                <Link href="/lab" className="np-btn np-btn-ghost">New theory →</Link>
              </div>
              {lab.length === 0 ? (
                <p className="ag-mine-empty">
                  Nothing saved yet. Test a theory in the <Link href="/lab">Lab</Link> and press{' '}
                  <strong>Save as an agent</strong> under the result.
                </p>
              ) : (
                <div className="ag-list">
                  {lab.map((a) => (
                    <LabAgentCard
                      key={a.id}
                      a={a}
                      trades={ok.trades.filter((t) => t.strategy_id === a.id)}
                      busy={busy === a.id}
                      error={cardError?.id === a.id ? cardError.msg : null}
                      onRun={() => act(a.id, 'PATCH', { run_status: 'running' })}
                      onPause={() => act(a.id, 'PATCH', { run_status: 'paused' })}
                      onArchive={() => {
                        if (window.confirm(`Archive "${a.name}"? Its record is kept; it stops running.`)) {
                          act(a.id, 'DELETE')
                        }
                      }}
                    />
                  ))}
                </div>
              )}
            </section>

            {handWritten.length > 0 && (
              <section className="ag-mine-section">
                <div className="tp-section-head">
                  <h2>Hand-written</h2>
                </div>
                <p className="ag-mine-empty">
                  Daemons on your machine — switched on and off there, not here. Paper: real
                  prices, simulated stakes. None has reached its verdict gate.
                </p>
                <AgentStats trades={handTrades} />
                <AgentSection
                  trades={handTrades}
                  strategies={handWritten.map(toStrategy)}
                  pressure={ok.pressure}
                  loading={false}
                />
              </section>
            )}
          </>
        )}
      </div>
    </AppShell>
  )
}
