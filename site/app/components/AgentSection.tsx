import { fmtPnl, fmtPct } from '../lib/helpers'
import type { PaperTrade, Strategy } from '../lib/supabase'
import { SectionWrap, SectionTitle, Spinner } from './ui'
import { TradeCard } from './TradeCard'

export function AgentSection({
  trades,
  strategies,
  loading,
}: {
  trades: PaperTrade[]
  strategies: Strategy[]
  loading: boolean
}) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  const active = trades.filter((t) => !t.resolved_at).sort(
    (a, b) => new Date(a.game_time ?? a.placed_at).getTime() - new Date(b.game_time ?? b.placed_at).getTime()
  )
  const settled = trades.filter((t) => !!t.resolved_at)
  const recentSettled = settled.slice(0, 10)
  const totalPnl = settled.reduce((s, t) => s + Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0), 0)
  const wins = settled.filter(t => t.result === 'won').length
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0

  return (
    <SectionWrap>
      <SectionTitle title="THE AGENT" sub="AI-POWERED EDGE DETECTION ON PREDICTION MARKETS" />

      {/* Description — accessible */}
      <div style={{
        maxWidth: '640px',
        margin: '0 auto 48px',
        textAlign: 'center',
      }}>
        <p style={{
          fontSize: '13px',
          color: 'var(--grey)',
          lineHeight: '1.9',
        }}>
          Our AI scans football prediction markets every day, comparing prices against
          its own mathematical models trained on 100,000+ matches.
          When the market price is wrong, the agent bets — and logs everything here
          with full transparency, in real time.
        </p>
      </div>

      {/* Overall stats */}
      {trades.length > 0 && (
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
            <div className="v">{settled.length > 0 ? `${wins}W / ${settled.length - wins}L` : '—'}</div>
            <div className="l">RECORD</div>
          </div>
          <div>
            <div className="v" style={{ color: settled.length > 0 && totalPnl >= 0 ? 'var(--green)' : settled.length > 0 ? 'var(--red)' : 'var(--grey)' }}>
              {settled.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—'}
            </div>
            <div className="l">P&amp;L</div>
          </div>
        </div>
      )}

      {/* Strategy table (merged from leaderboard) */}
      {strategies.length > 0 && (
        <>
          <div style={{ fontSize: '11px', letterSpacing: '3px', color: 'var(--grey)', marginBottom: '20px' }}>
            STRATEGIES
          </div>
          <div className="table-scroll" style={{ marginBottom: '40px' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>STRATEGY</th>
                  <th>POSITIONS</th>
                  <th>WIN RATE</th>
                  <th>YIELD</th>
                  <th>P&L</th>
                  <th>STATUS</th>
                </tr>
              </thead>
              <tbody>
                {[...strategies]
                  .sort((a, b) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))
                  .map((s) => {
                  const isActive = !s.retired_at
                  const pnlVal = s.total_pnl ?? 0
                  const yieldVal = s.yield_pct ?? 0

                  return (
                    <tr key={s.id}>
                      <td style={{ color: 'var(--white)', letterSpacing: '1px' }}>{s.name}</td>
                      <td>{s.total_bets ?? 0}</td>
                      <td>
                        {s.total_bets ? `${((s.win_rate ?? 0) * 100).toFixed(0)}%` : '—'}
                        {(s.total_bets ?? 0) > 0 && (
                          <div className="win-bar-bg">
                            <div className="win-bar" style={{ width: `${(s.win_rate ?? 0) * 100}%` }} />
                          </div>
                        )}
                      </td>
                      <td style={{ color: yieldVal >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {s.total_bets ? fmtPct(yieldVal) : '—'}
                      </td>
                      <td style={{ color: pnlVal >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {s.total_bets ? fmtPnl(pnlVal) : '—'}
                      </td>
                      <td>
                        <span className={`badge ${isActive ? 'badge-live' : 'badge-rejected'}`}>
                          {isActive ? 'LIVE' : 'RETIRED'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Positions */}
      {trades.length === 0 ? (
        <div className="empty-state">
          <div style={{ fontSize: '48px', color: 'var(--border)', marginBottom: '24px', fontWeight: 'bold', letterSpacing: '4px' }}>
            ◌
          </div>
          <div style={{ fontSize: '16px', letterSpacing: '3px', marginBottom: '12px' }}>
            NO POSITIONS YET
          </div>
          <div style={{ fontSize: '12px', color: 'var(--grey)', lineHeight: '1.8', maxWidth: '480px', margin: '0 auto' }}>
            The agent scans Polymarket football markets every morning.
            When it finds a mispricing, it logs a paper trade here — publicly, in real time.
          </div>
        </div>
      ) : (
        <>
          {active.length > 0 && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
                <div style={{
                  width: '8px', height: '8px', borderRadius: '50%',
                  background: 'var(--red)', animation: 'pulse 1.5s infinite',
                }} />
                <div style={{ fontSize: '11px', letterSpacing: '3px', color: 'var(--grey)' }}>
                  ACTIVE NOW — {active.length}
                </div>
              </div>
              {active.map((t) => <TradeCard key={t.id} trade={t} />)}
              <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '32px 0' }} />
            </>
          )}
          {recentSettled.length > 0 && (
            <>
              <div style={{ fontSize: '11px', letterSpacing: '3px', color: 'var(--grey)', marginBottom: '20px' }}>
                RECENTLY SETTLED — {settled.length} TOTAL
              </div>
              {recentSettled.map((t) => <TradeCard key={t.id} trade={t} />)}
            </>
          )}
        </>
      )}
    </SectionWrap>
  )
}
