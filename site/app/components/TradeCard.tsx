'use client'

import { useState } from 'react'
import { formatDate, formatDateTime } from '../lib/helpers'
import type { PaperTrade } from '../lib/supabase'

export function extractMatchName(trade: PaperTrade): string {
  if (trade.reasoning) {
    // NBA Elo reasoning: "NBA Elo [type]: Team A (1234) vs Team B (5678)..."
    const nba = trade.reasoning.match(/NBA Elo.*?:\s*(.+?)\s*\(\d+.*?\)\s+vs\s+(.+?)\s*\(\d+.*?\)/i)
    if (nba) return `${nba[1].trim()} vs ${nba[2].trim()}`
    // DC Model / MC Sim / WC agent reasoning: "X: Team A vs Team B —"
    const model = trade.reasoning.match(/(?:DC Model|MC Sim|WC):\s*(.+?)\s*[—\-]/i)
    if (model) return model[1].trim()
    const m2 = trade.reasoning.match(/Match:\s*(.+?)\s*\[/i)
    if (m2) return m2[1].trim()
  }
  // Try market_title with "vs"
  if (trade.market_title && trade.market_title !== '—') {
    const m = trade.market_title.match(/(?:Will\s+)?(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s+end|\s+win|\?|$)/i)
    if (m) return `${m[1].trim()} vs ${m[2].trim()}`
    const m2 = trade.market_title.match(/Will\s+(.+?)\s+win\b/i)
    if (m2) return m2[1].trim()
    return trade.market_title
  }
  return '—'
}

export function formatOutcome(outcome: string | null | undefined): string {
  if (!outcome) return '—'
  const o = outcome.toLowerCase()

  // Direct labels
  const direct: Record<string, string> = {
    'home': 'HOME WIN',
    'home_win': 'HOME WIN',
    'away': 'AWAY WIN',
    'away_win': 'AWAY WIN',
    'draw': 'DRAW',
    'ht_home_win': 'HOME WIN (1H)',
    'ht_draw': 'DRAW (1H)',
    'ht_away_win': 'AWAY WIN (1H)',
    'btts': 'BTTS',
    'no_btts': 'NO BTTS',
    'home_wins_by_2plus': 'HOME -1.5',
    'home_wins_by_3plus': 'HOME -2.5',
    'away_wins_by_2plus': 'AWAY -1.5',
    'away_wins_by_3plus': 'AWAY -2.5',
  }
  if (direct[o]) return direct[o]

  // Totals: "over_2_5", "over_2.5", "under_3_5", etc.
  const tot = o.match(/^(over|under)_(\d+)[._](\d+)$/)
  if (tot) return `${tot[1].toUpperCase()} ${tot[2]}.${tot[3]}`

  // "NOT <team> win" (No Bias strategy) — leave as-is but uppercase
  return outcome.toUpperCase()
}

export function TradeCard({ trade, live = false }: { trade: PaperTrade; live?: boolean }) {
  const [open, setOpen] = useState(false)
  const isResolved = !!trade.resolved_at
  const isWon      = trade.result === 'won'
  const isVoid     = trade.result === 'void'

  const src = trade.sharp_consensus_sources as Record<string, unknown> | null
  const score  = src?.score  as string | undefined
  const minute = src?.minute as number | undefined
  const isInPlay = !!score
  const isNba = trade.strategy_name?.toLowerCase().includes('nba') ||
                trade.reasoning?.includes('NBA Elo')

  const edgePp = Number(trade.expected_edge) * 100

  // $ accounting only in the Live Polymarket context (live=true). Paper-strategy
  // feeds always render units so the experiment stays measurable in 1u terms.
  const liveShares = Number(trade.pm_order_size ?? 0)
  const livePrice = Number(trade.pm_order_price ?? 0)
  const liveCost = liveShares * livePrice
  const isLiveTrade = live && !!trade.pm_live && liveShares > 0 && livePrice > 0

  const pnl = isLiveTrade && isResolved
    ? (isWon ? liveShares - liveCost : isVoid ? 0 : -liveCost)
    : Number(trade.payout_units ?? 0) - Number(trade.stake_units ?? 0)
  const pnlSuffix = isLiveTrade ? '' : 'u'
  const pnlPrefix = isLiveTrade ? '$' : ''

  const matchName = extractMatchName(trade)
  const entryPrice = Number(trade.entry_price)
  const entryOdds = trade.entry_odds ? Number(trade.entry_odds) : (entryPrice > 0 ? 1 / entryPrice : 0)
  const entryProb = entryPrice * 100

  const modelProb = Number(trade.model_probability) * 100
  const modelOdds = Number(trade.model_probability) > 0 ? (1 / Number(trade.model_probability)).toFixed(2) : '—'
  // Some strategies are deliberately model-free — FLB is a pure price filter
  // with no fair-value estimate. Rendering those as 0.0% / +0.0% reads as a
  // broken number rather than "not applicable".
  const hasModel = trade.model_probability != null && Number(trade.model_probability) > 0

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
            {' · '}{trade.game_time ? formatDateTime(trade.game_time) : formatDate(trade.placed_at)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
          {isNba && (
            <span style={{ fontSize: '10px', letterSpacing: '2px', color: '#f97316', border: '1px solid #f97316', padding: '2px 6px' }}>
              NBA
            </span>
          )}
          {isInPlay && (
            <span style={{ fontSize: '10px', letterSpacing: '2px', color: 'var(--accent)', border: '1px solid var(--accent)', padding: '2px 6px' }}>
              IN-PLAY
            </span>
          )}
          {isResolved ? (
            isVoid ? (
              <span className="badge" style={{ borderColor: 'var(--grey)', color: 'var(--grey)' }}>
                VOID
              </span>
            ) : (
              <span className="badge" style={{ borderColor: isWon ? 'var(--green)' : 'var(--red)', color: isWon ? 'var(--green)' : 'var(--red)' }}>
                {isWon ? 'WON' : 'LOST'}
              </span>
            )
          ) : (
            <span className="badge badge-status-live">OPEN</span>
          )}
        </div>
      </div>

      {/* Pick */}
      <div style={{ marginBottom: '20px', fontSize: '14px', color: 'var(--accent)', letterSpacing: '1px' }}>
        PICK: {formatOutcome(trade.outcome)}
      </div>

      {/* Stats grid */}
      <div className="rg-4" style={{ gap: '16px', marginBottom: '16px' }}>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>PM ODDS</div>
          <div style={{ fontSize: '20px' }}>{entryOdds > 0 ? entryOdds.toFixed(2) : '—'}</div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '2px' }}>{entryProb.toFixed(1)}%</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--accent)', letterSpacing: '2px', marginBottom: '6px' }}>OUR ODDS</div>
          <div style={{ fontSize: '20px' }}>{modelOdds}</div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '2px' }}>
            {hasModel ? `${modelProb.toFixed(1)}%` : 'no model'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>EDGE</div>
          {/* The sign came from a hard-coded "+", so a negative edge rendered as
              "+-1.8%" and was coloured green. It comes from the number now. */}
          <div
            style={{
              fontSize: '20px',
              color: !hasModel ? 'var(--grey)' : edgePp >= 0 ? 'var(--green)' : 'var(--red)',
            }}
          >
            {hasModel ? `${edgePp >= 0 ? '+' : ''}${edgePp.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>STAKE</div>
          <div style={{ fontSize: '20px' }}>
            {isLiveTrade ? `$${liveCost.toFixed(2)}` : `${Number(trade.stake_units ?? 1).toFixed(0)}u`}
          </div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginBottom: '6px' }}>P&L</div>
          <div style={{ fontSize: '20px', color: isVoid ? 'var(--grey)' : (isResolved ? (pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)') }}>
            {isVoid
              ? '—'
              : (isResolved
                  ? (pnlPrefix === '$'
                      ? `${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(2)}`
                      : `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}${pnlSuffix}`)
                  : 'OPEN')}
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
