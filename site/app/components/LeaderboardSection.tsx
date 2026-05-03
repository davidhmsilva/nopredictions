import { fmt, fmtPct, fmtPnl, fmtClv } from '../lib/helpers'
import type { Strategy, DbStats } from '../lib/supabase'
import { SectionWrap, SectionTitle, Spinner } from './ui'

export function LeaderboardSection({
  strategies,
  stats,
  loading,
}: {
  strategies: Strategy[]
  stats: DbStats | null
  loading: boolean
}) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  const totalBets = strategies.reduce((s, x) => s + (x.total_bets ?? 0), 0)
  const totalPnl = strategies.reduce((s, x) => s + (x.total_pnl ?? 0), 0)
  const allWins = strategies.reduce((s, x) => s + (x.wins ?? 0), 0)
  const avgClv =
    strategies.length > 0
      ? strategies.reduce((s, x) => s + (x.avg_clv ?? 0), 0) / strategies.length
      : 0

  return (
    <SectionWrap>
      <SectionTitle title="LEADERBOARD" sub="ALL STRATEGIES · RANKED BY CLV" />

      {/* Stats row */}
      <div className="rg-4" style={{ gap: '16px', marginBottom: '40px' }}>
        {[
          { label: 'TOTAL P&L', value: totalBets > 0 ? fmtPnl(totalPnl) : '—', green: totalPnl >= 0 },
          { label: 'AVG YIELD', value: totalBets > 0 ? fmtPct(strategies.reduce((s, x) => s + (x.yield_pct ?? 0), 0) / strategies.length) : '—', green: true },
          { label: 'AVG CLV', value: totalBets > 0 ? fmtClv(avgClv * 100) : '—', green: avgClv >= 0 },
          { label: 'TOTAL POSITIONS', value: totalBets > 0 ? String(totalBets) : '—', green: true },
        ].map((s) => (
          <div key={s.label} className="stat-box">
            <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '8px' }}>{s.label}</div>
            <div
              style={{
                fontSize: '28px',
                fontWeight: 'bold',
                color: s.value === '—' ? 'var(--grey)' : s.green ? 'var(--green)' : 'var(--red)',
              }}
            >
              {s.value}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--grey)', marginTop: '4px' }}>
              {s.label === 'TOTAL POSITIONS' && totalBets > 0 ? `${allWins} wins / ${totalBets}` : 'since launch'}
            </div>
          </div>
        ))}
      </div>

      {strategies.length === 0 ? (
        <div className="empty-state">
          <div style={{ fontSize: '14px', letterSpacing: '3px', marginBottom: '12px' }}>
            NO STRATEGIES RANKED YET
          </div>
          <div style={{ fontSize: '12px', color: 'var(--grey)', lineHeight: '1.8', maxWidth: '520px', margin: '0 auto 32px' }}>
            The leaderboard populates once the agent promotes its first signal to live execution.
            Every signal requires surviving the adversarial critic and a minimum of 200 positions
            before appearing here.
          </div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px' }}>
            DATA FOUNDATION READY ·{' '}
            <span style={{ color: 'var(--accent)' }}>
              {stats ? stats.matches.toLocaleString() : '—'} MATCHES
            </span>{' '}
            ·{' '}
            <span style={{ color: 'var(--accent)' }}>
              {stats ? stats.oddsRecords.toLocaleString() : '—'} ODDS RECORDS
            </span>{' '}
            ·{' '}
            <span style={{ color: 'var(--accent)' }}>
              {stats ? stats.leagues : '—'} LEAGUES
            </span>
          </div>
        </div>
      ) : (
        <div className="table-scroll"><table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>STRATEGY</th>
              <th>POSITIONS</th>
              <th>WIN RATE</th>
              <th>YIELD %</th>
              <th>AVG CLV</th>
              <th>P&L (UNITS)</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((s, i) => {
              const isActive = !s.retired_at
              const pnlColor = (s.total_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'
              const clvColor = (s.avg_clv ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'

              return (
                <tr key={s.id}>
                  <td style={{ color: 'var(--grey)', fontSize: '11px' }}>{i + 1}</td>
                  <td style={{ color: 'var(--white)', letterSpacing: '1px' }}>{s.name}</td>
                  <td>{s.total_bets ?? 0}</td>
                  <td>
                    {fmt(s.win_rate ?? 0)}%
                    <div className="win-bar-bg">
                      <div className="win-bar" style={{ width: `${s.win_rate ?? 0}%` }} />
                    </div>
                  </td>
                  <td style={{ color: (s.yield_pct ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {fmtPct(s.yield_pct ?? 0)}
                  </td>
                  <td style={{ color: clvColor }}>{fmtClv((s.avg_clv ?? 0) * 100)}</td>
                  <td style={{ color: pnlColor }}>{fmtPnl(s.total_pnl ?? 0)}</td>
                  <td>
                    <span className={`badge ${isActive ? 'badge-live' : 'badge-rejected'}`}>
                      {isActive ? 'LIVE' : 'RETIRED'}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table></div>
      )}
    </SectionWrap>
  )
}
