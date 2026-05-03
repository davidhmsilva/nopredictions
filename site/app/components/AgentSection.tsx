import { fmtClv } from '../lib/helpers'
import type { PaperTrade } from '../lib/supabase'
import { SectionWrap, SectionTitle, Spinner } from './ui'
import { TradeCard } from './TradeCard'

export function AgentSection({ trades, loading }: { trades: PaperTrade[]; loading: boolean }) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  const active = trades.filter((t) => !t.resolved_at)
  const settled = trades.filter((t) => !!t.resolved_at)
  const recentSettled = settled.slice(0, 10)
  const totalPnl = settled.reduce((s, t) => s + Number(t.payout_units ?? 0), 0)
  const wins = settled.filter(t => t.result === 'won').length
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0
  const tradesWithClv = trades.filter(t => t.clv != null)
  const avgClv = tradesWithClv.length > 0
    ? tradesWithClv.reduce((s, t) => s + Number(t.clv ?? 0), 0) / tradesWithClv.length
    : 0

  return (
    <SectionWrap>
      <SectionTitle title="THE AGENT" sub="AI-POWERED EDGE DETECTION ON POLYMARKET" />

      {/* Description */}
      <div style={{
        maxWidth: '640px',
        margin: '0 auto 48px',
        textAlign: 'center',
      }}>
        <p style={{
          fontSize: '13px',
          color: 'var(--grey)',
          lineHeight: '1.9',
          marginBottom: '24px',
        }}>
          The agent continuously scans Polymarket for football markets where prices
          diverge from sharp bookmaker consensus. It uses mathematical models to identify
          mispricings and logs every position with full transparency. No predictions, just edges.
        </p>
        <div style={{
          display: 'flex',
          gap: '24px',
          justifyContent: 'center',
          flexWrap: 'wrap',
        }}>
          {[
            { label: 'STRATEGY', value: 'PM vs Sharp Consensus' },
            { label: 'MODE', value: 'Paper Trading' },
            { label: 'VENUE', value: 'Polymarket' },
          ].map(s => (
            <div key={s.label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '4px' }}>{s.label}</div>
              <div style={{ fontSize: '12px', color: 'var(--accent)', letterSpacing: '1px' }}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Stats strip */}
      {trades.length > 0 && (
        <div className="lab-stats-strip" style={{ marginBottom: '40px' }}>
          <div>
            <div className="v">{trades.length}</div>
            <div className="l">POSITIONS</div>
          </div>
          <div>
            <div className="v">{settled.length > 0 ? `${winRate.toFixed(0)}%` : '—'}</div>
            <div className="l">WIN RATE</div>
          </div>
          <div>
            <div className="v" style={{ color: settled.length > 0 && totalPnl >= 0 ? 'var(--green)' : settled.length > 0 ? 'var(--red)' : 'var(--grey)' }}>
              {settled.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—'}
            </div>
            <div className="l">P&amp;L</div>
          </div>
          <div>
            <div className="v" style={{ color: avgClv >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {tradesWithClv.length > 0 ? `${avgClv >= 0 ? '+' : ''}${(avgClv * 100).toFixed(1)}¢` : '—'}
            </div>
            <div className="l">AVG CLV</div>
          </div>
        </div>
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
            The agent scans Polymarket football markets every morning and compares prices
            against sharp bookmaker consensus. When it finds a mispricing above its threshold,
            it logs a paper trade here — posted before kickoff.
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
