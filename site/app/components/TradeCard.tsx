'use client'

import { useState } from 'react'
import { fmtClv, formatDate } from '../lib/helpers'
import type { PaperTrade } from '../lib/supabase'

export function TradeCard({ trade }: { trade: PaperTrade }) {
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
