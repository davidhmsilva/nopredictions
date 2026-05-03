'use client'

import { useState, useEffect } from 'react'
import {
  fetchDbStats,
  fetchLeaderboard,
  fetchPaperTrades,
  type DbStats,
  type Strategy,
  type PaperTrade,
} from './lib/supabase'

// -- Helpers ----------------------------------------------------------------

function fmt(n: number, decimals = 1): string {
  return n.toFixed(decimals)
}

function fmtPct(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`
}

function fmtPnl(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n, 2)}u`
}

function fmtClv(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n, 1)}¢`
}

function colorClass(n: number): string {
  return n >= 0 ? 'text-green' : 'text-red'
}

function resultBadgeClass(result: string | null): string {
  switch (result) {
    case 'won':
      return 'badge badge-live'
    case 'lost':
      return 'badge badge-rejected'
    default:
      return 'badge badge-pending'
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

// -- Ticker -----------------------------------------------------------------

const TICKER_ITEMS = [
  { label: 'PRIMARY VENUE', value: 'POLYMARKET', up: true },
  { label: 'SHARP BENCHMARK', value: 'BETFAIR EXCHANGE', up: true },
  { label: 'HISTORICAL MEMORY', value: '16 YEARS', up: true },
  { label: 'MATCHES IN DATABASE', value: '124,000+', up: true },
  { label: 'ODDS RECORDS', value: '393,458', up: true },
  { label: 'LEAGUES TRACKED', value: '22', up: true },
  { label: 'POLYMARKET MARKETS', value: 'LIVE', up: true },
  { label: 'PINNACLE CLOSING ODDS', value: 'LOADED', up: true },
  { label: 'AGENT STATUS', value: 'ACTIVE', up: true },
  { label: 'HYPOTHESES TESTED', value: '7 · 0 PROMOTED', up: true },
  { label: 'IN-PLAY MONITOR', value: 'LIVE', up: true },
  { label: 'NARRATIVE', value: 'AI · PREDICTION MARKETS', up: true },
]

function Ticker() {
  return (
    <div className="ticker-bar">
      <div className="ticker-inner">
        {[...TICKER_ITEMS, ...TICKER_ITEMS].map((item, i) => (
          <span key={i} className={`ticker-item${item.up ? '' : ' down'}`}>
            {item.label} <b>{item.value}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

// -- Nav --------------------------------------------------------------------

type Section = 'home' | 'leaderboard' | 'strategies' | 'newsletter' | 'about'

const NAV_LINKS: { id: Section; label: string }[] = [
  { id: 'strategies', label: 'AGENT' },
  { id: 'leaderboard', label: 'LEADERBOARD' },
  { id: 'newsletter', label: 'NEWSLETTER' },
  { id: 'about', label: 'ABOUT' },
]

function Nav({
  section,
  setSection,
}: {
  section: Section
  setSection: (s: Section) => void
}) {
  return (
    <nav className="nav-container">
      {/* Logo */}
      <div
        style={{ display: 'flex', flexDirection: 'column', padding: '16px 0', cursor: 'pointer' }}
        onClick={() => setSection('home')}
      >
        <div
          className="nav-logo-title"
          style={{
            fontSize: '20px',
            fontWeight: 'bold',
            letterSpacing: '4px',
            color: 'var(--white)',
          }}
        >
          NOPREDICTIONS
        </div>
        <div
          className="nav-logo-sub"
          style={{ fontSize: '9px', letterSpacing: '3px', color: 'var(--grey)', marginTop: '2px' }}
        >
          AI VS POLYMARKET
        </div>
      </div>

      {/* Links */}
      <div className="nav-links-desktop">
        {NAV_LINKS.map((link) => (
          <button
            key={link.id}
            onClick={() => setSection(link.id)}
            style={{
              padding: '20px 24px',
              color: section === link.id ? 'var(--accent)' : 'var(--grey)',
              cursor: 'pointer',
              fontSize: '12px',
              letterSpacing: '2px',
              background: 'none',
              border: 'none',
              borderBottom: section === link.id ? '2px solid var(--accent)' : '2px solid transparent',
              fontFamily: 'var(--font)',
              transition: 'all 0.15s',
            }}
          >
            {link.label}
          </button>
        ))}
      </div>

      {/* CTA — desktop only */}
      <button
        className="nav-cta-desktop"
        onClick={() => setSection('newsletter')}
        style={{
          border: '1px solid var(--accent)',
          color: 'var(--accent)',
          padding: '8px 16px',
          fontFamily: 'var(--font)',
          fontSize: '11px',
          letterSpacing: '2px',
          cursor: 'pointer',
          background: 'transparent',
          transition: 'all 0.15s',
        }}
        onMouseOver={(e) => {
          e.currentTarget.style.background = 'var(--accent)'
          e.currentTarget.style.color = 'var(--bg)'
        }}
        onMouseOut={(e) => {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--accent)'
        }}
      >
        FOLLOW THE AGENT ↗
      </button>
    </nav>
  )
}

// -- MOBILE BOTTOM NAV ------------------------------------------------------

const MOBILE_NAV: { id: Section; icn: string; label: string }[] = [
  { id: 'home',        icn: '◆', label: 'HOME' },
  { id: 'strategies',  icn: '◇', label: 'AGENT' },
  { id: 'leaderboard', icn: '▲', label: 'RANK' },
  { id: 'about',       icn: '◌', label: 'ABOUT' },
]

function MobileNav({
  section,
  setSection,
}: {
  section: Section
  setSection: (s: Section) => void
}) {
  return (
    <div className="mobile-nav">
      {MOBILE_NAV.map((item) => (
        <button
          key={item.id}
          className={section === item.id ? 'active' : ''}
          onClick={() => setSection(item.id)}
        >
          <span className="icn">{item.icn}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  )
}

// -- Shared: Section wrapper -------------------------------------------------

function SectionWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="sec-wrap">
      {children}
    </div>
  )
}

function SectionTitle({ title, sub }: { title: string; sub: string }) {
  return (
    <>
      <div style={{ fontSize: '22px', letterSpacing: '4px', marginBottom: '8px' }}>{title}</div>
      <div style={{ color: 'var(--grey)', fontSize: '11px', letterSpacing: '2px', marginBottom: '32px' }}>
        {sub}
      </div>
    </>
  )
}

function Spinner() {
  return (
    <div style={{ textAlign: 'center', padding: '60px', color: 'var(--grey)', fontSize: '11px', letterSpacing: '3px' }}>
      LOADING...
    </div>
  )
}

// -- LIVE BETS --------------------------------------------------------------

function TradeCard({ trade }: { trade: PaperTrade }) {
  const [open, setOpen] = useState(false)
  const isResolved = !!trade.resolved_at
  const isWon      = trade.result === 'won'
  const clvColor   = (trade.clv ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'

  // Extract score + minute from sharp_consensus_sources if it's an in-play trade
  const src = trade.sharp_consensus_sources as Record<string, unknown> | null
  const score  = src?.score  as string | undefined
  const minute = src?.minute as number | undefined
  const isInPlay = !!score

  return (
    <div style={{ background: 'var(--bg2)', border: '1px solid var(--border)', padding: '24px', marginBottom: '16px' }}>

      {/* Header */}
      <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '16px', marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: '13px', color: 'var(--white)', letterSpacing: '1px', marginBottom: '4px' }}>
            {trade.market_title}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--grey)' }}>
            {trade.strategy_name}
            {isInPlay && score ? ` · IN-PLAY ${score} ${minute}'` : ''}
            {' · '}{formatDate(trade.placed_at)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
          {isInPlay && (
            <span style={{ fontSize: '10px', letterSpacing: '2px', color: 'var(--accent)', border: '1px solid var(--accent)', padding: '2px 6px' }}>
              IN-PLAY
            </span>
          )}
          {isResolved ? (
            <span className="badge" style={{ borderColor: isWon ? 'var(--green)' : 'var(--red)', color: isWon ? 'var(--green)' : 'var(--red)' }}>
              {isWon ? 'WON' : 'LOST'}
            </span>
          ) : (
            <span className="badge badge-status-live">● OPEN</span>
          )}
        </div>
      </div>

      {/* Pick */}
      <div style={{ marginBottom: '20px', fontSize: '14px', color: 'var(--accent)', letterSpacing: '1px' }}>
        PICK: {trade.outcome}
      </div>

      {/* Stats grid */}
      <div className="rg-4" style={{ gap: '16px', marginBottom: '16px' }}>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>ENTRY ODDS</div>
          <div style={{ fontSize: '20px' }}>{Number(trade.entry_odds).toFixed(2)}</div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '2px' }}>{(Number(trade.entry_price) * 100).toFixed(1)}¢</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>MODEL PROB</div>
          <div style={{ fontSize: '20px' }}>{(Number(trade.model_probability) * 100).toFixed(1)}%</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>EDGE</div>
          <div style={{ fontSize: '20px', color: 'var(--green)' }}>+{Number(trade.expected_edge).toFixed(1)}pp</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>CLV</div>
          <div style={{ fontSize: '20px', color: clvColor }}>
            {trade.clv != null ? fmtClv(Number(trade.clv) * 100) : 'PENDING'}
          </div>
        </div>
      </div>

      {/* Reasoning (collapsible) */}
      {trade.reasoning && (
        <div>
          <button
            onClick={() => setOpen(o => !o)}
            style={{ background: 'none', border: 'none', fontFamily: 'var(--font)', fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', cursor: 'pointer', padding: '0', marginBottom: open ? '12px' : '0' }}
          >
            {open ? '▲ HIDE REASONING' : '▼ AGENT REASONING'}
          </button>
          {open && (
            <div className="reasoning-box" style={{ marginTop: '8px', whiteSpace: 'pre-wrap', fontSize: '11px' }}>
              {trade.reasoning}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// -- LEADERBOARD ------------------------------------------------------------

function LeaderboardSection({
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

// -- STRATEGIES -------------------------------------------------------------

function AgentSection({ trades, loading }: { trades: PaperTrade[]; loading: boolean }) {
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

// -- NEWSLETTER -------------------------------------------------------------

function NewsletterSection() {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // TODO: wire to email provider (Resend, Mailchimp, etc.)
    setSubmitted(true)
  }

  return (
    <SectionWrap>
      <div style={{ maxWidth: '560px', margin: '60px auto', textAlign: 'center' }}>
        <h1 style={{ fontSize: '28px', letterSpacing: '4px', marginBottom: '16px' }}>
          FOLLOW THE AGENT
        </h1>
        <p style={{ color: 'var(--grey)', fontSize: '12px', lineHeight: '1.8', marginBottom: '40px' }}>
          Get weekly updates: every position the agent opens, edge reports,
          and P&amp;L results. No noise. Just the data.
        </p>

        {submitted ? (
          <div
            style={{
              background: 'var(--bg2)',
              border: '1px solid var(--green)',
              padding: '24px',
              color: 'var(--green)',
              letterSpacing: '2px',
              fontSize: '13px',
            }}
          >
            ✓ YOU&apos;RE IN. WE&apos;LL BE IN TOUCH.
          </div>
        ) : (
          <>
            <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 0, marginBottom: '16px' }}>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                style={{
                  flex: 1,
                  background: 'var(--bg2)',
                  border: '1px solid var(--border)',
                  borderRight: 'none',
                  color: 'var(--white)',
                  fontFamily: 'var(--font)',
                  fontSize: '13px',
                  padding: '14px 16px',
                  outline: 'none',
                }}
              />
              <button
                type="submit"
                style={{
                  background: 'var(--accent)',
                  color: 'var(--bg)',
                  border: 'none',
                  padding: '14px 24px',
                  fontFamily: 'var(--font)',
                  fontSize: '12px',
                  letterSpacing: '2px',
                  cursor: 'pointer',
                  fontWeight: 'bold',
                }}
              >
                SUBSCRIBE
              </button>
            </form>
            <div style={{ fontSize: '11px', color: 'var(--grey)' }}>
              No spam. Unsubscribe anytime. Every pick posted before kickoff.
            </div>
          </>
        )}

        {/* What you get */}
        <div
          style={{
            textAlign: 'left',
            marginTop: '60px',
            borderTop: '1px solid var(--border)',
            paddingTop: '40px',
          }}
        >
          <h3
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '24px',
            }}
          >
            WHAT YOU GET EVERY WEEK
          </h3>
          {[
            {
              title: 'All live positions',
              text: 'Every position the agent opens, with full reasoning, before the match starts. No hindsight.',
            },
            {
              title: 'Edge reports',
              text: 'What mispricings the agent found this week — which markets diverged from sharp consensus and by how much.',
            },
            {
              title: 'Weekly P&L',
              text: 'Honest accounting. Units won, units lost, CLV captured. Good weeks and bad weeks alike.',
            },
            {
              title: 'Research graveyard',
              text: "Strategies that failed and exactly why. The failures are as important as the wins.",
            },
          ].map((item) => (
            <div key={item.title} style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
              <div style={{ color: 'var(--accent)', fontSize: '16px', flexShrink: 0 }}>→</div>
              <div style={{ fontSize: '12px', color: 'var(--grey)', lineHeight: '1.6' }}>
                <strong style={{ color: 'var(--white)' }}>{item.title}</strong> — {item.text}
              </div>
            </div>
          ))}
        </div>
      </div>
    </SectionWrap>
  )
}

// -- ABOUT ------------------------------------------------------------------

function AboutSection({ stats, loading }: { stats: DbStats | null; loading: boolean }) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  const earliestYear = stats?.earliestMatch
    ? new Date(stats.earliestMatch).getFullYear()
    : 2010
  const latestYear = stats?.latestMatch
    ? new Date(stats.latestMatch).getFullYear()
    : new Date().getFullYear()

  return (
    <SectionWrap>
      <div style={{ maxWidth: '720px' }}>
        {/* Intro */}
        <div
          className="rg-1-2"
          style={{
            gap: '48px',
            marginBottom: '48px',
            paddingBottom: '48px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="about-avatar">◈</div>
          <div>
            <div style={{ fontSize: '22px', letterSpacing: '3px', marginBottom: '8px' }}>
              DAVID SILVA
            </div>
            <div
              style={{
                color: 'var(--grey)',
                fontSize: '12px',
                letterSpacing: '2px',
                marginBottom: '24px',
              }}
            >
              16 YEARS IN MARKETS · NOW WITH AN AI CO-PILOT
            </div>
            <div style={{ fontSize: '13px', color: '#aaa', lineHeight: '1.9' }}>
              <p style={{ marginBottom: '16px' }}>
                I&apos;ve been involved in sports betting and prediction markets for 16 years. Not as
                a hobby — seriously. I&apos;ve studied closing lines, tracked edges, managed
                bankrolls through long losing runs, and felt the discipline required to not blow
                everything when you&apos;re wrong.
              </p>
              <p style={{ marginBottom: '16px' }}>
                I&apos;ve had wins. I&apos;ve had periods where everything I touched turned to
                nothing. I know what a real edge looks like — and I know how hard it is to find
                one consistently.
              </p>
              <p>
                With AI getting genuinely capable, I had one question: can an AI agent do what I
                couldn&apos;t do systematically? Can it find edges in prediction markets that
                human intuition misses? This project is the answer to that question — run live,
                in public, with real stakes.
              </p>
            </div>
          </div>
        </div>

        {/* Real stats */}
        <div
          className="rg-4"
          style={{
            gap: '16px',
            marginBottom: '48px',
          }}
        >
          {[
            { num: '16', label: 'YEARS IN MARKETS' },
            {
              num: stats ? stats.matches.toLocaleString() : '—',
              label: 'MATCHES IN DB',
            },
            {
              num: stats ? stats.oddsRecords.toLocaleString() : '—',
              label: 'ODDS RECORDS',
            },
            {
              num: `${earliestYear}→${latestYear}`,
              label: 'DATA RANGE',
            },
          ].map((s) => (
            <div
              key={s.label}
              style={{
                background: 'var(--bg2)',
                border: '1px solid var(--border)',
                padding: '20px',
                textAlign: 'center',
              }}
            >
              <div
                style={{
                  fontSize: s.num.length > 6 ? '18px' : '28px',
                  color: 'var(--accent)',
                  marginBottom: '4px',
                  fontWeight: 'bold',
                }}
              >
                {s.num}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px' }}>
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* Leagues covered */}
        <div
          style={{
            border: '1px solid var(--border)',
            padding: '24px',
            marginBottom: '32px',
          }}
        >
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '20px',
            }}
          >
            LEAGUES IN DATABASE ({stats?.leagues ?? 27})
          </div>
          <div
            className="rg-3"
            style={{
              gap: '8px',
              fontSize: '11px',
              color: 'var(--grey)',
            }}
          >
            {[
              'Premier League', 'Championship', 'League One', 'League Two',
              'La Liga', 'Segunda División', 'Serie A', 'Serie B',
              'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2',
              'Eredivisie', 'Pro League (Belgium)', 'Liga Portugal', 'Süper Lig',
              'Super League (Greece)', 'Scottish Premiership', 'Scottish Championship',
              'Scottish League One', 'Scottish League Two',
              'Champions League', 'Europa League',
              'World Cup', 'European Championship', 'Copa América', 'Nations League',
            ].map((l) => (
              <div key={l} style={{ padding: '4px 0', borderBottom: '1px solid #161616' }}>
                <span style={{ color: 'var(--accent)' }}>·</span> {l}
              </div>
            ))}
          </div>
        </div>

        {/* Rules */}
        <div style={{ border: '1px solid var(--border)', padding: '24px' }}>
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '20px',
            }}
          >
            THE RULES THE AGENT FOLLOWS
          </div>
          {[
            {
              n: '1',
              title: 'No lookahead bias.',
              text: 'Every data point used must have been available before kickoff. The agent can only use information a bettor would have had at the time.',
            },
            {
              n: '2',
              title: 'CLV is king.',
              text: 'Positive ROI with negative Closing Line Value = luck. Positive CLV with negative ROI (short-term) = potential edge. We always measure both.',
            },
            {
              n: '3',
              title: 'Minimum 200 selections',
              text: 'before any strategy conclusion. No cherry-picking a 15-bet hot streak.',
            },
            {
              n: '4',
              title: 'Every failure gets logged.',
              text: 'Every position is logged — wins and losses. No quiet deletions.',
            },
            {
              n: '5',
              title: 'All picks posted before kickoff.',
              text: "No retroactive claims. The agent puts its reasoning on the table before the match starts.",
            },
          ].map((rule) => (
            <div
              key={rule.n}
              style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}
            >
              <div
                style={{
                  color: 'var(--accent)',
                  fontSize: '18px',
                  flexShrink: 0,
                  width: '24px',
                }}
              >
                {rule.n}
              </div>
              <div style={{ fontSize: '12px', color: '#aaa', lineHeight: '1.7' }}>
                <strong style={{ color: 'var(--white)' }}>{rule.title}</strong> {rule.text}
              </div>
            </div>
          ))}
        </div>
      </div>
    </SectionWrap>
  )
}

// -- FOOTER -----------------------------------------------------------------

function Footer({ setSection }: { setSection: (s: Section) => void }) {
  return (
    <footer
      style={{
        borderTop: '1px solid var(--border)',
        padding: '24px 32px',
        color: 'var(--grey)',
        fontSize: '11px',
        marginTop: '80px',
      }}
    >
      <div className="footer-inner">
      <div>NOPREDICTIONS © 2026 · AI VS POLYMARKET · NOT FINANCIAL ADVICE</div>
      <div className="footer-links">
        <a
          href="https://x.com"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--grey)', textDecoration: 'none' }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          X / TWITTER
        </a>
        <button
          onClick={() => setSection('newsletter')}
          style={{
            color: 'var(--grey)',
            background: 'none',
            border: 'none',
            fontFamily: 'var(--font)',
            fontSize: '11px',
            cursor: 'pointer',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          NEWSLETTER
        </button>
        <button
          onClick={() => setSection('about')}
          style={{
            color: 'var(--grey)',
            background: 'none',
            border: 'none',
            fontFamily: 'var(--font)',
            fontSize: '11px',
            cursor: 'pointer',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          ABOUT
        </button>
      </div>
      </div>
    </footer>
  )
}


// -- HOME -------------------------------------------------------------------


function HomeSection({
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

  return <HomeSectionInner
    stats={stats}
    trades={trades}
    latestTrade={latestTrade}
    top3={top3}
    settledTrades={settledTrades}
    activeTrades={activeTrades}
    totalPnl={totalPnl}
    avgClv={avgClv}
    setSection={setSection}
    loading={loading}
  />
}

function HomeSectionInner({
  stats,
  trades,
  latestTrade,
  top3,
  settledTrades,
  activeTrades,
  totalPnl,
  avgClv,
  setSection,
  loading,
}: {
  stats: DbStats | null
  trades: PaperTrade[]
  latestTrade: PaperTrade | null
  top3: Strategy[]
  settledTrades: PaperTrade[]
  activeTrades: PaperTrade[]
  totalPnl: number
  avgClv: number
  setSection: (s: Section) => void
  loading: boolean
}) {
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

// -- ROOT PAGE --------------------------------------------------------------

const VALID_SECTIONS: Section[] = ['home', 'leaderboard', 'strategies', 'newsletter', 'about']

function readSectionFromHash(): Section {
  if (typeof window === 'undefined') return 'home'
  const h = window.location.hash.replace(/^#/, '') as Section
  return VALID_SECTIONS.includes(h) ? h : 'home'
}

export default function Page() {
  const [section, setSectionState] = useState<Section>('home')
  const [stats, setStats] = useState<DbStats | null>(null)
  const [trades, setTrades] = useState<PaperTrade[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loadingStats, setLoadingStats] = useState(true)
  const [loadingTrades, setLoadingTrades] = useState(true)
  const [loadingStrategies, setLoadingStrategies] = useState(true)

  // ── Hash-based routing — so back/swipe-back navigates sections ──
  // Sync initial section from URL hash on mount, listen to popstate for back/forward.
  useEffect(() => {
    setSectionState(readSectionFromHash())
    const onPop = () => setSectionState(readSectionFromHash())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // setSection wrapper: pushes to history so back button works
  const setSection = (s: Section) => {
    if (s === section) return
    if (typeof window !== 'undefined') {
      const newUrl = s === 'home' ? window.location.pathname : `#${s}`
      window.history.pushState({ section: s }, '', newUrl)
      window.scrollTo(0, 0)
    }
    setSectionState(s)
  }

  useEffect(() => {
    fetchDbStats()
      .then(setStats)
      .finally(() => setLoadingStats(false))

    fetchPaperTrades()
      .then(setTrades)
      .finally(() => setLoadingTrades(false))

    fetchLeaderboard()
      .then(setStrategies)
      .finally(() => setLoadingStrategies(false))
  }, [])

  return (
    <>
      <Ticker />
      <Nav section={section} setSection={setSection} />

      {section === 'home' && (
        <HomeSection
          stats={stats}
          trades={trades}
          strategies={strategies}
          setSection={setSection}
          loading={loadingStats || loadingTrades || loadingStrategies}
        />
      )}
      {section === 'leaderboard' && (
        <LeaderboardSection
          strategies={strategies}
          stats={stats}
          loading={loadingStrategies}
        />
      )}
      {section === 'strategies' && (
        <AgentSection trades={trades} loading={loadingTrades} />
      )}
      {section === 'newsletter' && <NewsletterSection />}
      {section === 'about' && <AboutSection stats={stats} loading={loadingStats} />}

      <Footer setSection={setSection} />
      <MobileNav section={section} setSection={setSection} />
    </>
  )
}
