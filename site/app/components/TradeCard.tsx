'use client'

import { useState } from 'react'
import { formatDate } from '../lib/helpers'
import type { PaperTrade } from '../lib/supabase'

function extractMatchName(trade: PaperTrade): string {
  if (trade.market_title && trade.market_title !== '—') {
    const m = trade.market_title.match(/(?:Will\s+)?(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s+end|\s+win|\?|$)/i)
    if (m) return `${m[1].trim()} vs ${m[2].trim()}`
    return trade.market_title
  }
  if (trade.reasoning) {
    const m = trade.reasoning.match(/Match:\s*(.+?)\s*\[/i)
    if (m) return m[1].trim()
    const m2 = trade.reasoning.match(/^(.+?\s+vs\.?\s+.+?)[,\s]/i)
    if (m2) return m2[1].trim()
  }
  return '—'
}

export function TradeCard({ trade }: { trade: PaperTrade }) {
  const [open, setOpen] = useState(false)
  const isResolved = !!trade.resolved_at
  const isWon      = trade.result === 'won'

  const src = trade.sharp_consensus_sources as Record<string, unknown> | null
  const score  = src?.score  as string | undefined
  const minute = src?.minute as number | undefined
  const isInPlay = !!score

  const edgePp = Number(trade.expected_edge) * 100
  const pnl = Number(trade.payout_units ?? 0)

  const matchName = extractMatchName(trade)
  const entryPrice = Number(trade.entry_price)
  const entryOdds = trade.entry_odds ? Number(trade.entry_odds) : (entryPrice > 0 ? 1 / entryPrice : 0)
  const entryProb = entryPrice * 100

  const modelProb = Number(trade.model_probability) * 100
  const modelOdds = Number(trade.model_probability) > 0 ? (1 / Number(trade.model_probability)).toFixed(2) : '—'

  return (
    <div style={{ background: 'var(--bg2)', border: '1px solid var(--border)', padding: '24px', marginBottom: '16px' }}>

      {/* Header */}
      <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '16px', marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: '14px', color: 'var(--white)', letterSpacing: '1px', marginBottom: '4px', fontWeight: 'bold' }}>
            {matchName}
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
            <span className="badge badge-status-live">OPEN</span>
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
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>PM ODDS</div>
          <div style={{ fontSize: '20px' }}>{entryOdds > 0 ? entryOdds.toFixed(2) : '—'}</div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '2px' }}>{entryProb.toFixed(1)}% implied</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--accent)', letterSpacing: '2px', marginBottom: '6px' }}>OUR PRICE</div>
          <div style={{ fontSize: '20px' }}>{modelProb.toFixed(1)}%</div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '2px' }}>{modelOdds} fair odds</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>EDGE</div>
          <div style={{ fontSize: '20px', color: 'var(--green)' }}>+{edgePp.toFixed(1)}pp</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>P&L</div>
          <div style={{ fontSize: '20px', color: isResolved ? (pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)' }}>
            {isResolved ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}u` : 'OPEN'}
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
            {open ? '▲ HIDE REASONING' : '▼ WHY THIS TRADE?'}
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
