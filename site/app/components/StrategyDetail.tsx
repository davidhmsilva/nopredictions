'use client'

import { fmtPnl, fmtPct, formatDate, formatDateTime } from '../lib/helpers'
import type { PaperTrade, Strategy } from '../lib/supabase'
import { formatOutcome, extractMatchName } from './TradeCard'

export function StrategyDetail({
  strategy,
  trades,
  onBack,
}: {
  strategy: Strategy
  trades: PaperTrade[]
  onBack: () => void
}) {
  const stratTrades = trades
    .filter((t) => t.strategy_id === strategy.id)
    .sort((a, b) => new Date(b.game_time ?? b.placed_at).getTime() - new Date(a.game_time ?? a.placed_at).getTime())

  const settled = stratTrades.filter((t) => !!t.resolved_at)
  const active = stratTrades.filter((t) => !t.resolved_at)
  const wins = settled.filter((t) => t.result === 'won').length
  const losses = settled.filter((t) => t.result === 'lost').length
  const voids = settled.filter((t) => t.result === 'void').length
  const totalPnl = settled.reduce((s, t) => s + Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0), 0)
  const totalStaked = settled.reduce((s, t) => s + Number(t.stake_units ?? 0), 0)
  const yieldPct = totalStaked > 0 ? (totalPnl / totalStaked) * 100 : 0
  const avgEdge = stratTrades.length > 0
    ? stratTrades.reduce((s, t) => s + Number(t.expected_edge ?? 0), 0) / stratTrades.length * 100
    : 0
  const avgClv = settled.filter(t => t.clv != null)
  const avgClvVal = avgClv.length > 0
    ? avgClv.reduce((s, t) => s + Number(t.clv!), 0) / avgClv.length
    : null

  return (
    <div>
      {/* Back button */}
      <button
        onClick={onBack}
        style={{
          background: 'none',
          border: '1px solid var(--border)',
          color: 'var(--grey)',
          fontFamily: 'var(--font)',
          fontSize: '11px',
          letterSpacing: '2px',
          cursor: 'pointer',
          padding: '8px 16px',
          marginBottom: '24px',
        }}
      >
        ← ALL STRATEGIES
      </button>

      {/* Strategy name */}
      <div style={{ fontSize: '18px', letterSpacing: '3px', color: 'var(--white)', marginBottom: '8px' }}>
        {strategy.name}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--grey)', marginBottom: '32px' }}>
        {strategy.retired_at ? 'RETIRED' : 'LIVE'}
        {' · '}{stratTrades.length} TOTAL TRADES
      </div>

      {/* Stats strip */}
      <div className="lab-stats-strip" style={{ marginBottom: '40px' }}>
        <div>
          <div className="v" style={{ color: 'var(--accent)' }}>{active.length}</div>
          <div className="l">OPEN</div>
        </div>
        <div>
          <div className="v">{settled.length}</div>
          <div className="l">SETTLED</div>
        </div>
        <div>
          <div className="v">{settled.length > 0 ? `${wins}W / ${losses}L` : '—'}</div>
          <div className="l">RECORD</div>
        </div>
        <div>
          <div className="v" style={{ color: settled.length > 0 ? (totalPnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)' }}>
            {settled.length > 0 ? fmtPnl(totalPnl) : '—'}
          </div>
          <div className="l">P&L</div>
        </div>
        <div>
          <div className="v" style={{ color: settled.length > 0 ? (yieldPct >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)' }}>
            {settled.length > 0 ? fmtPct(yieldPct) : '—'}
          </div>
          <div className="l">YIELD</div>
        </div>
        <div>
          <div className="v" style={{ color: 'var(--green)' }}>
            {avgEdge > 0 ? `+${avgEdge.toFixed(1)}%` : '—'}
          </div>
          <div className="l">AVG EDGE</div>
        </div>
      </div>

      {/* Trades list */}
      {stratTrades.length === 0 ? (
        <div style={{ textAlign: 'center', color: 'var(--grey)', padding: '40px 0', fontSize: '12px', letterSpacing: '2px' }}>
          NO TRADES YET
        </div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>MATCH</th>
                <th>PICK</th>
                <th>PM ODDS</th>
                <th>MODEL</th>
                <th>EDGE</th>
                <th>RESULT</th>
                <th>P&L</th>
                <th>DATE</th>
              </tr>
            </thead>
            <tbody>
              {stratTrades.map((t) => {
                const pnl = Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0)
                const isResolved = !!t.resolved_at
                const isWon = t.result === 'won'
                const isVoid = t.result === 'void'
                const entryOdds = t.entry_odds ? Number(t.entry_odds) : (Number(t.entry_price) > 0 ? 1 / Number(t.entry_price) : 0)
                const modelOdds = Number(t.model_probability) > 0 ? 1 / Number(t.model_probability) : 0
                const edgePp = Number(t.expected_edge) * 100

                return (
                  <tr key={t.id}>
                    <td style={{ color: 'var(--white)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {extractMatchName(t)}
                    </td>
                    <td style={{ color: 'var(--accent)', fontSize: '11px' }}>
                      {formatOutcome(t.outcome)}
                    </td>
                    <td>{entryOdds > 0 ? entryOdds.toFixed(2) : '—'}</td>
                    <td>{modelOdds > 0 ? modelOdds.toFixed(2) : '—'}</td>
                    <td style={{ color: 'var(--green)' }}>+{edgePp.toFixed(1)}%</td>
                    <td>
                      {isResolved ? (
                        isVoid ? (
                          <span style={{ color: 'var(--grey)' }}>VOID</span>
                        ) : (
                          <span style={{ color: isWon ? 'var(--green)' : 'var(--red)' }}>
                            {isWon ? 'WON' : 'LOST'}
                          </span>
                        )
                      ) : (
                        <span style={{ color: 'var(--accent)' }}>OPEN</span>
                      )}
                    </td>
                    <td style={{ color: isVoid ? 'var(--grey)' : (isResolved ? (pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)') }}>
                      {isVoid ? '—' : (isResolved ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}u` : '—')}
                    </td>
                    <td style={{ fontSize: '11px', color: 'var(--grey)' }}>
                      {t.game_time ? formatDate(t.game_time) : formatDate(t.placed_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
