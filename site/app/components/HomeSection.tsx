'use client'

import { useState, useEffect } from 'react'
import { fmtPnl, fmtClv, formatDate } from '../lib/helpers'
import type { Section } from '../lib/types'
import type { DbStats, Strategy, PaperTrade } from '../lib/supabase'
import { Spinner } from './ui'
import { TradeCard } from './TradeCard'

export function HomeSection({
  stats,
  trades,
  strategies,
  setSection,
  loading,
}: {
  stats: DbStats | null
  trades: PaperTrade[]
  strategies: Strategy[]
  setSection: (s: Section) => void
  loading: boolean
}) {
  const latestTrade = trades[0] ?? null
  const top3 = strategies.slice(0, 3)
  const settledTrades = trades.filter(t => !!t.resolved_at)
  const totalPnl = settledTrades.reduce((s, t) => s + Number(t.payout_units ?? 0), 0)
  const tradesWithClv = trades.filter(t => t.clv != null)
  const avgClv = tradesWithClv.length > 0
    ? tradesWithClv.reduce((s, t) => s + Number(t.clv ?? 0), 0) / tradesWithClv.length
    : 0
  const activeTrades = trades.filter(t => !t.resolved_at)

  const [email, setEmail] = useState('')
  const [subscribed, setSubscribed] = useState(false)
  const [cursor, setCursor] = useState(true)

  // Blinking cursor
  useEffect(() => {
    const t = setInterval(() => setCursor(c => !c), 530)
    return () => clearInterval(t)
  }, [])

  function handleSubscribe(e: React.FormEvent) {
    e.preventDefault()
    setSubscribed(true)
  }

  return (
    <div>

      {/* ── HERO (compact) ── */}
      <div className="home-hero">
        <div className="eyebrow">● AI AGENT · POLYMARKET · LIVE</div>
        <h1>
          NO PREDICTIONS.<br />
          <span className="accent">JUST EDGES.</span>
          <span style={{ color: 'var(--accent)', opacity: cursor ? 1 : 0 }}>_</span>
        </h1>
        <p>
          An AI agent hunting football mispricings on Polymarket — comparing prices against
          sharp bookmaker consensus to find edges the market hasn't priced in.
          Every position, every failure: public.
        </p>
        <div className="cta-row">
          <button className="btn-primary" onClick={() => setSection('strategies')}>
            ● SEE THE AGENT
          </button>
          <button className="btn-secondary" onClick={() => setSection('leaderboard')}>
            LEADERBOARD →
          </button>
        </div>
      </div>

      {/* ── LATEST POSITION (the actual hook) ── */}
      <div className="home-block">
        <div className="block-eyebrow">
          <span>● LATEST POSITION</span>
          {trades.length > 0 && (
            <button onClick={() => setSection('strategies')}>
              ALL {trades.length} →
            </button>
          )}
        </div>
        {loading ? (
          <Spinner />
        ) : latestTrade ? (
          <TradeCard trade={latestTrade} />
        ) : (
          <div style={{
            background: 'var(--bg)', border: '1px solid var(--border)',
            padding: '32px 20px', textAlign: 'center',
          }}>
            <div style={{ fontSize: '13px', color: 'var(--white)', letterSpacing: '2px', marginBottom: '8px' }}>
              NO POSITIONS YET
            </div>
            <div style={{ fontSize: '12px', color: 'var(--grey)', lineHeight: '1.7', maxWidth: '420px', margin: '0 auto' }}>
              The agent scans Polymarket football markets daily. First trade lands
              when it finds a price that diverges from sharp consensus.
            </div>
          </div>
        )}
      </div>

      {/* ── AGENT STATS ── */}
      <div className="home-block alt">
        <div className="block-eyebrow"><span>AGENT STATS</span></div>
        <div className="lab-stats-strip">
          <div>
            <div className="v">{trades.length}</div>
            <div className="l">POSITIONS</div>
          </div>
          <div>
            <div className="v" style={{ color: activeTrades.length > 0 ? 'var(--green)' : 'var(--grey)' }}>
              {activeTrades.length}
            </div>
            <div className="l">ACTIVE</div>
          </div>
          <div>
            <div className="v" style={{ color: settledTrades.length > 0 && totalPnl >= 0 ? 'var(--green)' : settledTrades.length > 0 ? 'var(--red)' : 'var(--grey)' }}>
              {settledTrades.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—'}
            </div>
            <div className="l">P&amp;L</div>
          </div>
          <div>
            <div className="v" style={{ color: avgClv >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {trades.length > 0 ? `${avgClv >= 0 ? '+' : ''}${(avgClv * 100).toFixed(1)}¢` : '—'}
            </div>
            <div className="l">AVG CLV</div>
          </div>
        </div>
      </div>

      {/* ── MINI LEADERBOARD ── */}
      <div className="home-block">
        <div className="block-eyebrow">
          <span>LEADERBOARD</span>
          <button onClick={() => setSection('leaderboard')}>FULL TABLE →</button>
        </div>
        {top3.length > 0 ? (
          <>
            {top3.map((s, i) => {
              const pnl = s.total_pnl ?? 0
              const clv = s.avg_clv ?? 0
              return (
                <div key={s.id} className="mini-lb-row">
                  <div className="rank">#{i + 1}</div>
                  <div className="name">{s.name}</div>
                  <div className="mini-lb-metrics" style={{ display: 'contents' }}>
                    <div>
                      <div className="metric-label">PNL</div>
                      <div style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtPnl(pnl)}</div>
                    </div>
                    <div>
                      <div className="metric-label">CLV</div>
                      <div style={{ color: clv >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtClv(clv * 100)}</div>
                    </div>
                    <div>
                      <div className="metric-label">N</div>
                      <div>{s.total_bets ?? 0}</div>
                    </div>
                  </div>
                </div>
              )
            })}
          </>
        ) : (
          <div style={{
            background: 'var(--bg)', border: '1px solid var(--border)',
            padding: '24px 20px', fontSize: '13px', color: 'var(--grey)', lineHeight: '1.7',
          }}>
            <span style={{ color: 'var(--white)' }}>No strategies ranked yet.</span>{' '}
            The agent is scanning Polymarket for edges against sharp bookmaker consensus.
            Strategies appear here once they accumulate enough trades to measure performance.
          </div>
        )}
      </div>

      {/* ── RECENT POSITIONS ── */}
      <div className="home-block alt">
        <div className="block-eyebrow">
          <span>● RECENT POSITIONS</span>
          <button onClick={() => setSection('strategies')}>SEE ALL →</button>
        </div>
        {trades.length > 0 ? (
          trades.slice(0, 3).map((t) => (
            <div key={t.id} className="hyp-row">
              <div className="hyp-title">{t.market_title || t.outcome}</div>
              <div className="hyp-meta">
                <span className={t.result === 'won' ? 'badge badge-live' : t.result === 'lost' ? 'badge badge-rejected' : 'badge badge-pending'}>
                  {t.result ? t.result.toUpperCase() : 'OPEN'}
                </span>
                <span>{formatDate(t.placed_at)}</span>
              </div>
            </div>
          ))
        ) : (
          <div style={{ fontSize: '12px', color: 'var(--grey)' }}>No positions yet — the agent is scanning.</div>
        )}
      </div>

      {/* ── WHAT IS THIS (compact) ── */}
      <div className="home-block">
        <div className="block-eyebrow"><span>WHAT IS THIS?</span></div>
        <p className="what-is-this">
          An AI agent scans <strong>Polymarket</strong> football markets daily, comparing
          prices against <strong>sharp bookmaker consensus</strong> to find mispricings.
          When it detects an edge, it logs a paper trade — posted before kickoff with full
          reasoning. Two waves at once: AI and prediction markets.
          No retroactive claims, no quiet failures.{' '}
          <button onClick={() => setSection('about')}>The full project →</button>
        </p>
      </div>

      {/* ── NEWSLETTER (inline, compact) ── */}
      <div className="home-block alt">
        <div className="block-eyebrow"><span>● FOLLOW THE EXPERIMENT</span></div>
        {subscribed ? (
          <div style={{
            background: 'var(--bg)', border: '1px solid var(--green)',
            padding: '16px', color: 'var(--green)', letterSpacing: '2px', fontSize: '13px',
          }}>
            ✓ YOU&apos;RE IN.
          </div>
        ) : (
          <>
            <form onSubmit={handleSubscribe} style={{ display: 'flex', gap: 0, marginBottom: '8px', maxWidth: '480px' }}>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                style={{
                  flex: 1, minWidth: 0,
                  background: 'var(--bg)', border: '1px solid var(--border)', borderRight: 'none',
                  color: 'var(--white)', fontFamily: 'var(--font)',
                  fontSize: '13px', padding: '12px 14px', outline: 'none',
                }}
              />
              <button type="submit" className="btn-primary" style={{ padding: '12px 18px' }}>
                SUBSCRIBE
              </button>
            </form>
            <div style={{ fontSize: '11px', color: 'var(--grey)' }}>
              Weekly: live positions, edge reports, P&amp;L updates. No spam.
            </div>
          </>
        )}
      </div>

    </div>
  )
}
