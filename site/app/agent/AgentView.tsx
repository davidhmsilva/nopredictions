'use client'

/** One agent, drawn like a Game Center page: a hero with what it is and its
 *  controls, the tiles, the curve, the trades. Presentational — the page
 *  fetches and acts, this draws. */

import Link from 'next/link'
import type { AgentDetail, AgentSummary } from '../lib/agents'
import { dateText, dayTimeText, oddsText, useOddsFormat } from '../lib/display'
import { EquityChart, StatusBadge, ago, pct, pickOf, settledOf, sinceText, tone, units } from './parts'

/** Any verdict needs this many settled bets (CLAUDE.md, rule 4). */
const VERDICT_N = 200

const RESULT: Record<string, { label: string; cls: string }> = {
  won: { label: 'WON', cls: 'is-good' },
  lost: { label: 'LOST', cls: 'is-live' },
  void: { label: 'VOID', cls: '' },
}

export interface AgentActions {
  busy: boolean
  error: string | null
  onRun: () => void
  onPause: () => void
  onArchive: () => void
}

function Hero({ a, actions }: { a: AgentSummary; actions?: AgentActions }) {
  // Only agents built in the Lab are switched from here; the others run on
  // their own schedule. The page does not say which is which — it just offers
  // the controls that work.
  const controllable = a.source === 'lab' && actions
  const theory = a.theory && a.theory !== a.name ? a.theory : null
  const interp = a.interpretation && a.interpretation !== a.name && a.interpretation !== a.theory ? a.interpretation : null
  return (
    <div className="gc-hero">
      <div className="gc-hero-top">
        <span className="gc-hero-comp">{sinceText(a)}</span>
        <StatusBadge a={a} />
      </div>
      <h1 className="ag-hero-name">{a.name}</h1>
      {theory && <p className="ag-hero-theory">&ldquo;{theory}&rdquo;</p>}
      {interp && <p className="ag-hero-theory">Tested as: {interp}</p>}
      <div className="ag-hero-foot">
        <span>
          {a.n_open > 0 ? `${a.n_open} open · ` : ''}Last bet {ago(a.last_bet_at)}
        </span>
        {controllable && (
          <div className="ag-hero-actions">
            {a.run_status === 'running' ? (
              <button className="np-btn" disabled={actions.busy} onClick={actions.onPause}>Pause</button>
            ) : (
              <button
                className="np-btn np-btn-primary"
                disabled={actions.busy || Boolean(a.run_blocker)}
                title={a.run_blocker ?? undefined}
                onClick={actions.onRun}
              >
                Run it
              </button>
            )}
            <button className="np-btn np-btn-ghost" disabled={actions.busy} onClick={actions.onArchive}>Archive</button>
          </div>
        )}
      </div>
      {a.run_blocker && <p className="ag-note"><b>Cannot run live yet.</b> {a.run_blocker}</p>}
      {actions?.error && <p className="ag-hero-error">{actions.error}</p>}
    </div>
  )
}

function Tiles({ a }: { a: AgentSummary }) {
  const settled = settledOf(a)
  const hit = settled ? (a.wins / settled) * 100 : null
  return (
    <div className="gc-tiles ag-tiles">
      <div className="gc-tile">
        <span className="gc-tile-k">Settled bets</span>
        <span className="gc-tile-v np-num">{settled}</span>
        <span className="gc-tile-sub">{a.n_open > 0 ? `${a.n_open} open` : 'none open'}</span>
      </div>
      <div className="gc-tile">
        <span className="gc-tile-k">Record</span>
        <span className="gc-tile-v np-num">{settled ? `${a.wins}–${a.losses}` : '—'}</span>
        <span className="gc-tile-sub">won–lost</span>
        {/* The same two numbers as a length: how much of the record is won.
            Both sides are coloured, so it reads as a record and not as
            progress towards something. */}
        {hit != null && (
          <span className="ag-wl" role="img" aria-label={`${a.wins} won, ${a.losses} lost`}>
            <span className="ag-wl-won" style={{ width: `${hit}%` }} />
          </span>
        )}
      </div>
      <div className="gc-tile">
        <span className="gc-tile-k">Win rate</span>
        <span className="gc-tile-v np-num">{hit != null ? `${hit.toFixed(1)}%` : '—'}</span>
        <span className="gc-tile-sub">{settled ? `${a.wins} of ${settled}` : '—'}</span>
      </div>
      <div className="gc-tile">
        <span className="gc-tile-k">P&amp;L</span>
        <span className={`gc-tile-v np-num ${tone(settled ? a.pl_units : null)}`}>{settled ? units(a.pl_units) : '—'}</span>
        <span className="gc-tile-sub">{settled ? `on ${a.staked.toFixed(0)}u staked` : '—'}</span>
      </div>
      <div className="gc-tile">
        <span className="gc-tile-k">Yield</span>
        <span className={`gc-tile-v np-num ${tone(a.yield_pct)}`}>{a.yield_pct != null ? pct(a.yield_pct) : '—'}</span>
        <span className="gc-tile-sub">profit ÷ staked</span>
      </div>
      <div className="gc-tile">
        <span className="gc-tile-k">CLV</span>
        <span className={`gc-tile-v np-num ${tone(a.avg_clv)}`}>{a.avg_clv != null ? pct(a.avg_clv * 100) : '—'}</span>
        <span className="gc-tile-sub">{a.avg_clv != null ? 'vs the closing price' : 'not measured'}</span>
      </div>
    </div>
  )
}

export function AgentView({ detail, actions }: { detail: AgentDetail; actions?: AgentActions }) {
  const fmt = useOddsFormat()
  const { agent: a, trades, curve } = detail
  const settled = settledOf(a)
  const bt = a.backtest

  return (
    <>
      <Link href="/agent" className="ag-back">← Your agents</Link>
      <Hero a={a} actions={actions} />

      <section className="gc-section">
        <Tiles a={a} />
        {settled > 0 && settled < VERDICT_N && (
          <p className="ag-note">
            {settled} of the {VERDICT_N} settled bets a verdict needs — until then this is a record,
            not a result. A run of wins or losses this short is mostly the draw.
          </p>
        )}
      </section>

      <section className="gc-section">
        <h2 className="gc-h2">Profit and loss</h2>
        <EquityChart curve={curve} />
      </section>

      {bt && (
        <section className="gc-section">
          <h2 className="gc-h2">What history said</h2>
          <p className="gc-quiet">
            {bt.stats.n > 0 ? (
              <>
                Replayed over past seasons before it was saved:{' '}
                <span className="np-num">{bt.stats.n.toLocaleString('en-US')}</span> bets, yield{' '}
                <span className={`np-num ${tone(bt.stats.yieldPct)}`}>{pct(bt.stats.yieldPct, 2)}</span> ±
                <span className="np-num">{bt.stats.ci95Pct.toFixed(2)}</span>
                {bt.stats.clvPct != null && (
                  <>
                    , CLV <span className="np-num">{pct(bt.stats.clvPct, 2)}</span>
                  </>
                )}{' '}
                — <b>{bt.verdict.label}</b>. The record above is the forward test: the same rule on
                games that had not been played when it was saved.
              </>
            ) : (
              'No past game fitted this rule, so its only test is the record above.'
            )}
          </p>
        </section>
      )}

      <section className="gc-section">
        <h2 className="gc-h2">Bets</h2>
        {trades.length === 0 ? (
          <p className="gc-quiet">
            {a.run_status === 'running'
              ? 'Running — no game has fitted the rule yet.'
              : 'No bets yet.'}
          </p>
        ) : (
          <>
            <div className="sc-table-wrap">
              <table className="sc-table">
                <thead>
                  <tr>
                    <th className="ag-c-placed">Placed</th>
                    <th>Bet</th>
                    <th className="ag-hide-m">At entry</th>
                    <th className="ag-c-num">Odds</th>
                    <th className="ag-c-status">Result</th>
                    <th className="ag-c-num">P&amp;L</th>
                    <th className="ag-c-num ag-hide-m">CLV</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => {
                    const { pick, event } = pickOf(t)
                    const r = t.result ? RESULT[t.result] : null
                    const c = t.context
                    const at = c
                      ? [
                          c.entry_minute != null ? `${c.entry_minute}'` : null,
                          c.goals_at_entry != null ? `${c.goals_at_entry} goal${c.goals_at_entry === 1 ? '' : 's'}` : null,
                          c.pressure_index != null ? `pressure ${Math.round(c.pressure_index)}` : null,
                        ].filter(Boolean).join(' · ')
                      : ''
                    return (
                      <tr key={t.id}>
                        <td className="ag-c-placed np-num">
                          {dayTimeText(new Date(t.placed_at))}
                          <span className="ag-row-sub">{dateText(new Date(t.placed_at))}</span>
                        </td>
                        <td>
                          <span className="ag-t-pick">
                            {pick}
                            {t.live_money && (
                              <span className="np-badge is-warn ag-real" title="This bet was placed with real money">REAL</span>
                            )}
                          </span>
                          {event && <span className="ag-t-event">{event}</span>}
                        </td>
                        <td className="ag-hide-m ag-t-ctx">{at || '—'}</td>
                        <td className="ag-c-num np-num">{oddsText(t.entry_odds, fmt)}</td>
                        <td className="ag-c-status">
                          {r ? <span className={`np-badge ${r.cls}`}>{r.label}</span> : <span className="np-badge is-info">OPEN</span>}
                        </td>
                        <td className={`ag-c-num np-num ${tone(t.pl_units)}`}>{t.pl_units != null ? units(t.pl_units) : '—'}</td>
                        <td className={`ag-c-num ag-hide-m np-num ${tone(t.clv)}`}>{t.clv != null ? pct(t.clv * 100) : '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {a.n_trades > trades.length && (
              <p className="ag-note">
                The latest {trades.length} of {a.n_trades.toLocaleString('en-US')} bets. The tiles and
                the curve count all of them.
              </p>
            )}
          </>
        )}
      </section>
    </>
  )
}
