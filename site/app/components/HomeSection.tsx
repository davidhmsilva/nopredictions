'use client'

import { fmtPnl, formatDate, formatDateTime, byRecordThenPnl } from '../lib/helpers'
import type { Section } from '../lib/types'
import type { DbStats, Strategy, PaperTrade } from '../lib/supabase'
import { Spinner } from './ui'
import { TradeCard, extractMatchName } from './TradeCard'

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
  // Ranked by P&L, but an agent with nothing settled sorts LAST rather than
  // at 0.00u — otherwise a newly registered arm outranks every real record on
  // the strength of having never bet, and takes a homepage slot off one that
  // has.
  const top3 = [...strategies].sort(byRecordThenPnl).slice(0, 3)
  const settledTrades = trades.filter(t => !!t.resolved_at)
  const totalPnl = settledTrades.reduce((s, t) => s + Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0), 0)
  const wins = settledTrades.filter(t => t.result === 'won').length
  const winRate = settledTrades.length > 0 ? (wins / settledTrades.length) * 100 : 0
  const activeTrades = trades.filter(t => !t.resolved_at)

  return (
    <div>

      {/* ── HERO ── */}
      <div className="home-hero">
        {/* "LIVE" used to sit here. It is directly contradicted by the banner
            above this page: the agent is paper and has been since 2026-08-09. */}
        <div className="eyebrow">● AI AGENT · PREDICTION MARKETS · PAPER</div>
        <h1>
          NO PREDICTIONS.<br />
          <span className="accent">JUST EDGES.</span>
        </h1>
        <p>
          An AI agent hunting sports mispricings on prediction markets — using mathematical models
          trained on 139,000+ matches across football and NBA to find prices the market got wrong.
          Every position, every failure: public.
        </p>
        <div className="cta-row">
          <button className="btn-primary" onClick={() => setSection('strategies')}>
            ● SEE THE AGENT
          </button>
          <a href="/" className="btn-secondary">
            TODAY&apos;S BOARDS →
          </a>
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
              The agent scans Polymarket sports markets daily. First trade lands
              when it finds a price the market got wrong.
            </div>
          </div>
        )}
      </div>

      {/* ── AGENT STATS ── */}
      <div className="home-block alt">
        <div className="block-eyebrow" style={{ justifyContent: 'center' }}><span>AGENT STATS</span></div>
        <div className="lab-stats-strip">
          <div>
            <div className="v" style={{ color: 'var(--accent)' }}>{activeTrades.length}</div>
            <div className="l">OPEN</div>
          </div>
          <div>
            <div className="v">{settledTrades.length}</div>
            <div className="l">SETTLED</div>
          </div>
          <div>
            <div className="v">{settledTrades.length > 0 ? `${wins}W / ${settledTrades.length - wins}L` : '—'}</div>
            <div className="l">RECORD</div>
          </div>
          <div>
            <div className="v" style={{ color: settledTrades.length > 0 && totalPnl >= 0 ? 'var(--green)' : settledTrades.length > 0 ? 'var(--red)' : 'var(--grey)' }}>
              {settledTrades.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—'}
            </div>
            <div className="l">P&amp;L</div>
          </div>
        </div>
      </div>

      {/* ── AGENTS OVERVIEW ── */}
      <div className="home-block">
        <div className="block-eyebrow">
          <span>AGENTS</span>
          <button onClick={() => setSection('strategies')}>FULL DETAILS →</button>
        </div>
        {top3.length > 0 ? (
          <>
            {top3.map((s, i) => {
              const pnl = s.total_pnl ?? 0
              const wr = s.total_bets ? `${((s.win_rate ?? 0) * 100).toFixed(0)}%` : '—'
              return (
                <div key={s.id} className="mini-lb-row">
                  <div className="rank">#{i + 1}</div>
                  <div className="name">{s.name}</div>
                  <div className="mini-lb-metrics">
                    <div>
                      <div className="metric-label">PNL</div>
                      <div style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtPnl(pnl)}</div>
                    </div>
                    <div>
                      <div className="metric-label">WIN RATE</div>
                      <div>{wr}</div>
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
            <span style={{ color: 'var(--white)' }}>No agents ranked yet.</span>{' '}
            The agent is scanning Polymarket for mispriced markets.
            Agents appear here once they accumulate enough trades.
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
              <div className="hyp-title">{extractMatchName(t)}</div>
              <div className="hyp-meta">
                <span className={t.result === 'won' ? 'badge badge-live' : t.result === 'lost' ? 'badge badge-rejected' : 'badge badge-pending'}>
                  {t.result ? t.result.toUpperCase() : 'OPEN'}
                </span>
                <span>{t.game_time ? formatDateTime(t.game_time) : formatDate(t.placed_at)}</span>
              </div>
            </div>
          ))
        ) : (
          <div style={{ fontSize: '12px', color: 'var(--grey)' }}>No positions yet — the agent is scanning.</div>
        )}
      </div>

      {/* ── WHAT IS THIS (compact) ── */}
      <div className="home-block">
        <div className="block-eyebrow" style={{ justifyContent: 'center' }}><span>WHAT IS THIS?</span></div>
        <p className="what-is-this">
          An AI agent scans sports <strong>prediction markets</strong> daily, using
          mathematical models trained on <strong>139,000+ real matches</strong> across football
          and NBA to find mispricings. When it detects the market got a price wrong, it logs a paper trade
          — logged publicly in real time, with full reasoning.
          No retroactive claims, no quiet failures.
        </p>
      </div>

    </div>
  )
}
