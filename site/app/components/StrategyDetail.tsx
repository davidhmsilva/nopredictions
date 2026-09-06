'use client'

import { useEffect, useState } from 'react'
import { fmtPnl, fmtPct, formatDate, formatDateTime } from '../lib/helpers'
import type { PaperTrade, PressureTrade, Strategy } from '../lib/supabase'
import {
  PRESSURE_STRATEGY_IDS,
  PRESSURE_V2_STRATEGY_ID,
  V2_WINDOW,
  fetchPressureTrades,
  inV2Window,
} from '../lib/supabase'
import { formatOutcome, extractMatchName } from './TradeCard'

/**
 * The two clock cells for a Live Pressure Overs row: when we bought, and when
 * the goal came.
 *
 * The goal minute is only meaningful on a winner — a loser has no goal to date,
 * so it shows NO GOAL rather than an em dash that could be read as missing data.
 * A minute measured off our own 60s polling is marked with a leading ~ and can
 * never exceed 90; only the api-football source carries stoppage time.
 */
function PressureClock({
  row,
  isWon,
  isResolved,
}: {
  row?: PressureTrade
  isWon: boolean
  isResolved: boolean
}) {
  const grey = { color: 'var(--grey)', fontSize: '11px' }
  if (!row) {
    return (<><td style={grey}>—</td><td style={grey}>—</td></>)
  }
  const approx = row.goal_minute_source === 'poll'
  const waited =
    row.goal_minute != null ? row.goal_minute - row.entry_minute : null

  return (
    <>
      <td style={{ color: 'var(--white)', fontSize: '11px' }}>
        {row.entry_minute}&apos;
      </td>
      <td style={{ fontSize: '11px' }}>
        {row.goal_minute != null ? (
          <span
            style={{ color: 'var(--green)' }}
            title={
              approx
                ? 'Minute we first observed the new score (60s polling, capped at 90)'
                : 'Exact goal minute from api-football, including stoppage time'
            }
          >
            {approx ? '~' : ''}
            {row.goal_minute}&apos;
            {waited != null && waited >= 0 && (
              // NOT "+5": in football that reads as stoppage time (90+5). This
              // is how long the bet waited, so it has to say so.
              <span style={{ color: 'var(--grey)', marginLeft: '6px', fontSize: '10px' }}>
                {waited}&apos; wait
              </span>
            )}
          </span>
        ) : isResolved && !isWon ? (
          <span style={{ color: 'var(--grey)' }}>NO GOAL</span>
        ) : (
          <span style={grey}>—</span>
        )}
      </td>
    </>
  )
}

export function StrategyDetail({
  strategy,
  trades,
  onBack,
}: {
  strategy: Strategy
  trades: PaperTrade[]
  onBack: () => void
}) {
  // Scroll to top whenever a new strategy is opened (desktop + mobile).
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  }, [strategy.id])

  // Both pressure agents bet on WHEN a goal arrives, so the clock is the point
  // of the strategy, not decoration. It is not on paper_trades — see
  // fetchPressureTrades — so it is fetched only for these strategies.
  const isPressure = PRESSURE_STRATEGY_IDS.includes(strategy.id)
  const [pressure, setPressure] = useState<Map<number, PressureTrade>>(new Map())
  useEffect(() => {
    if (!isPressure) return
    let live = true
    fetchPressureTrades().then((m) => { if (live) setPressure(m) })
    return () => { live = false }
  }, [isPressure])

  // Two strategies own no paper_trades of their own and resolve their rows from
  // someone else's. 9 ("Live Polymarket") takes every trade actually submitted
  // on-chain, whatever produced it; 19 ("v2") takes strategy 16's own entries
  // that land in the minute/odds window, from the date it started counting.
  //
  // The v2 predicate needs the entry minute, which lives on v_pressure_trades
  // and arrives a moment after the page does — so this list is empty on the
  // first paint while the header, which reads the DB view, is already right.
  const isV2 = strategy.id === PRESSURE_V2_STRATEGY_ID
  const stratTrades = trades
    .filter((t) =>
      strategy.id === 9 ? !!t.pm_live
        : isV2 ? inV2Window(t, pressure.get(t.id)?.entry_minute)
        : t.strategy_id === strategy.id
    )
    .sort((a, b) => new Date(b.game_time ?? b.placed_at).getTime() - new Date(a.game_time ?? a.placed_at).getTime())

  // Active = open positions. For Live Polymarket we also exclude
  // cancelled / expired / failed orders since they're no longer on chain.
  const active = stratTrades.filter((t) =>
    !t.resolved_at &&
    (strategy.id !== 9 || ['matched', 'live'].includes(t.pm_order_status ?? ''))
  )
  const filledCount = active.filter(t => t.pm_order_status === 'matched').length
  const restingCount = active.filter(t => t.pm_order_status === 'live').length

  // Use authoritative stats from DB view (strategy object) — trades array is capped at 200
  const totalBets = strategy.total_bets ?? 0
  const wins = strategy.wins ?? 0
  const losses = strategy.losses ?? 0
  let totalPnl = strategy.total_pnl ?? 0
  let yieldPct = strategy.yield_pct ?? 0
  // For Live Polymarket recompute realised P&L in $ from actual fills, not units.
  if (strategy.id === 9) {
    const settled = stratTrades.filter(t => t.resolved_at && t.pm_order_size && t.pm_order_price)
    let pnl$ = 0, cost$ = 0
    for (const t of settled) {
      const sh = Number(t.pm_order_size ?? 0)
      const px = Number(t.pm_order_price ?? 0)
      const c = sh * px
      cost$ += c
      if (t.result === 'won') pnl$ += sh - c
      else if (t.result === 'lost') pnl$ -= c
    }
    totalPnl = pnl$
    yieldPct = cost$ > 0 ? (pnl$ / cost$) * 100 : 0
  }
  const avgEdge = stratTrades.length > 0
    ? stratTrades.reduce((s, t) => s + Number(t.expected_edge ?? 0), 0) / stratTrades.length * 100
    : 0

  // Live Polymarket only: unrealized mark-to-market metrics for matched positions.
  const isLive = strategy.id === 9
  const matchedTrades = stratTrades.filter(t => t.pm_cash_pnl !== null && t.pm_cash_pnl !== undefined && !t.resolved_at)
  const unrealizedPnl = matchedTrades.reduce((s, t) => s + Number(t.pm_cash_pnl ?? 0), 0)
  const openValue = matchedTrades.reduce((s, t) => s + Number(t.pm_current_value ?? 0), 0)
  // Total $ staked across every live trade (open + settled, resting + matched).
  const totalLiveStaked = isLive
    ? stratTrades
        .filter(t => t.pm_live && Number(t.pm_order_size ?? 0) > 0)
        .reduce((s, t) => s + Number(t.pm_order_size ?? 0) * Number(t.pm_order_price ?? 0), 0)
    : 0
  // Cost basis is derived so it stays consistent with API: value − pnl = initialValue.
  const openCost = openValue - unrealizedPnl
  const unrealizedPct = openCost > 0 ? (unrealizedPnl / openCost) * 100 : 0

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
        ← ALL AGENTS
      </button>

      {/* Strategy name */}
      <div style={{ fontSize: '18px', letterSpacing: '3px', color: 'var(--white)', marginBottom: '8px' }}>
        {strategy.name}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--grey)', marginBottom: isV2 ? '16px' : '32px' }}>
        {strategy.retired_at ? 'RETIRED' : 'LIVE'}
        {' · '}{stratTrades.length} TRADES LOADED
      </div>

      {/* v2 is a filter over another agent, chosen by looking at the data it is
          now being judged on. Saying so on the page is not a disclaimer — it is
          the reason the arm starts empty instead of opening on the number that
          made it look worth tracking. */}
      {isV2 && (
        <div
          style={{
            fontSize: '11px',
            lineHeight: '1.8',
            color: 'var(--grey)',
            border: '1px solid var(--border, #2a2a2a)',
            padding: '14px 16px',
            marginBottom: '32px',
            maxWidth: '640px',
          }}
        >
          A filter over <span style={{ color: 'var(--white)' }}>Live Pressure Overs</span>, not a
          second agent — it buys nothing of its own. Every entry that agent makes between minute{' '}
          <span style={{ color: 'var(--white)' }}>{V2_WINDOW.minMinute}&apos;–{V2_WINDOW.maxMinute}&apos;</span>{' '}
          at odds{' '}
          <span style={{ color: 'var(--white)' }}>
            {V2_WINDOW.minOdds.toFixed(2)}–{(V2_WINDOW.maxOddsExclusive - 0.01).toFixed(2)}
          </span>{' '}
          is counted here too, so this P&L is a slice of that one and is left out of the totals.
          <br />
          <br />
          The window was picked as the best cell of a minute × odds grid over 112 settled entries.
          Reshuffled noise finds an equally good cell{' '}
          <span style={{ color: 'var(--white)' }}>half the time (p = 0.39)</span>, and the same
          pocket measured across 564 fixtures is worth{' '}
          <span style={{ color: 'var(--white)' }}>+1.34pp, CI [−3.46, +6.28]</span> against a 2.89pp
          taker fee. So the honest prior is zero to slightly negative. It counts from{' '}
          {formatDate(new Date(V2_WINDOW.from).toISOString())} forward — the trades it was chosen on
          are deliberately not in the record.
        </div>
      )}

      {/* Stats strip — 6 cells, so use 3-column variant (3+3 balanced). */}
      <div className="lab-stats-strip cols-3" style={{ marginBottom: '40px' }}>
        <div>
          <div className="v" style={{ color: 'var(--accent)' }}>{active.length}</div>
          <div className="l">OPEN</div>
          {strategy.id === 9 && active.length > 0 && (
            <div style={{ fontSize: '10px', color: 'var(--grey)', marginTop: '4px', letterSpacing: '1px' }}>
              {filledCount} FILLED · {restingCount} RESTING
            </div>
          )}
        </div>
        <div>
          <div className="v">{totalBets}</div>
          <div className="l">SETTLED</div>
        </div>
        <div>
          <div className="v">{totalBets > 0 ? `${wins}W / ${losses}L` : '—'}</div>
          <div className="l">RECORD</div>
        </div>
        <div>
          <div className="v" style={{ color: totalBets > 0 ? (totalPnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)' }}>
            {totalBets > 0
              ? (strategy.id === 9
                  ? `${totalPnl >= 0 ? '+$' : '-$'}${Math.abs(totalPnl).toFixed(2)}`
                  : fmtPnl(totalPnl))
              : '—'}
          </div>
          <div className="l">{strategy.id === 9 ? 'REALISED P&L' : 'P&L'}</div>
        </div>
        <div>
          <div className="v" style={{ color: totalBets > 0 ? (yieldPct >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)' }}>
            {totalBets > 0 ? fmtPct(yieldPct) : '—'}
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

      {/* Live Polymarket: real-time mark-to-market block */}
      {isLive && matchedTrades.length > 0 && (
        <div style={{
          border: '1px solid var(--border)',
          padding: '16px 20px',
          marginBottom: '40px',
          background: 'rgba(0,0,0,0.2)',
        }}>
          <div style={{ fontSize: '11px', letterSpacing: '2px', color: 'var(--grey)', marginBottom: '12px' }}>
            ON-CHAIN MARK-TO-MARKET · {matchedTrades.length} MATCHED POSITIONS
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
            <div>
              <div style={{
                fontSize: '20px',
                color: unrealizedPnl >= 0 ? 'var(--green)' : 'var(--red)',
                fontWeight: 600,
              }}>
                {unrealizedPnl >= 0 ? '+$' : '-$'}{Math.abs(unrealizedPnl).toFixed(2)}
                <span style={{ fontSize: '12px', marginLeft: '6px', opacity: 0.7 }}>
                  ({unrealizedPct >= 0 ? '+' : ''}{unrealizedPct.toFixed(1)}%)
                </span>
              </div>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginTop: '4px' }}>
                UNREALIZED P&L
              </div>
            </div>
            <div>
              <div style={{ fontSize: '20px', color: 'var(--white)' }}>
                ${openValue.toFixed(2)}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginTop: '4px' }}>
                CURRENT VALUE
              </div>
            </div>
            <div>
              <div style={{ fontSize: '20px', color: 'var(--white)' }}>
                ${openCost.toFixed(2)}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px', marginTop: '4px' }}>
                COST BASIS
              </div>
            </div>
          </div>
          <div style={{
            marginTop: '14px',
            paddingTop: '12px',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '11px',
            color: 'var(--grey)',
            letterSpacing: '1px',
          }}>
            <span>TOTAL STAKED (open + settled)</span>
            <span style={{ color: 'var(--white)' }}>${totalLiveStaked.toFixed(2)}</span>
          </div>
        </div>
      )}

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
                {isPressure && <th>ENTRY</th>}
                {isPressure && <th>GOAL</th>}
                <th>PM ODDS</th>
                <th>MODEL</th>
                <th>EDGE</th>
                <th>STAKE</th>
                <th>RESULT</th>
                <th>P&L</th>
                <th>DATE</th>
              </tr>
            </thead>
            <tbody>
              {stratTrades.map((t) => {
                const isResolved = !!t.resolved_at
                const isWon = t.result === 'won'
                const isVoid = t.result === 'void'
                // For live (on-chain) trades, P&L = $ math (shares - cost when won, -cost when lost).
                const liveShares = Number(t.pm_order_size ?? 0)
                const livePrice = Number(t.pm_order_price ?? 0)
                const liveCost = liveShares * livePrice
                // $ accounting only inside the Live Polymarket strategy (id=9).
                // Paper strategies (DC, Sim, …) always stay in units, even if the
                // trade was also mirrored on-chain.
                const isLiveTrade = isLive && !!t.pm_live && liveShares > 0 && livePrice > 0
                const pnl = isLiveTrade && isResolved
                  ? (isWon ? liveShares - liveCost : isVoid ? 0 : -liveCost)
                  : Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0)
                const pnlUnit = isLiveTrade ? '$' : 'u'
                const entryOdds = t.entry_odds ? Number(t.entry_odds) : (Number(t.entry_price) > 0 ? 1 / Number(t.entry_price) : 0)
                const modelOdds = Number(t.model_probability) > 0 ? 1 / Number(t.model_probability) : 0
                const edgePp = Number(t.expected_edge) * 100

                return (
                  <tr key={t.id}>
                    <td style={{ color: 'var(--white)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {/* Pressure entries have no pm_markets row to join, so
                          extractMatchName returns nothing and the column reads
                          as a full column of em-dashes. The teams are already
                          in the view we fetched for the clock. */}
                      {extractMatchName(t) === '—' && pressure.get(t.id)
                        ? `${pressure.get(t.id)!.home} vs ${pressure.get(t.id)!.away}`
                        : extractMatchName(t)}
                    </td>
                    <td style={{ color: 'var(--accent)', fontSize: '11px' }}>
                      {formatOutcome(t.outcome)}
                    </td>
                    {isPressure && <PressureClock row={pressure.get(t.id)} isWon={isWon} isResolved={isResolved} />}
                    <td>{entryOdds > 0 ? entryOdds.toFixed(2) : '—'}</td>
                    <td>{modelOdds > 0 ? modelOdds.toFixed(2) : '—'}</td>
                    <td style={{ color: 'var(--green)' }}>+{edgePp.toFixed(1)}%</td>
                    <td style={{ color: 'var(--grey)', fontSize: '11px' }}>
                      {isLiveTrade
                        ? `$${liveCost.toFixed(2)}`
                        : `${Number(t.stake_units ?? 1).toFixed(0)}u`}
                    </td>
                    <td>
                      {isResolved ? (
                        isVoid ? (
                          <span style={{ color: 'var(--grey)' }}>VOID</span>
                        ) : (
                          <span style={{ color: isWon ? 'var(--green)' : 'var(--red)' }}>
                            {isWon ? 'WON' : 'LOST'}
                          </span>
                        )
                      ) : isLiveTrade ? (
                        t.pm_order_status === 'matched' ? (
                          <span style={{ color: 'var(--green)' }}>FILLED</span>
                        ) : t.pm_order_status === 'live' ? (
                          <span style={{ color: 'var(--accent)' }}>RESTING</span>
                        ) : (
                          <span style={{ color: 'var(--grey)', fontSize: '10px' }}>
                            {(t.pm_order_status ?? 'OPEN').toUpperCase().replace('EXPIRED-', '')}
                          </span>
                        )
                      ) : (
                        <span style={{ color: 'var(--accent)' }}>OPEN</span>
                      )}
                    </td>
                    <td style={{ color: isVoid ? 'var(--grey)' : (isResolved ? (pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--grey)') }}>
                      {isVoid
                        ? '—'
                        : (isResolved
                          ? (pnlUnit === '$'
                              ? `${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(2)}`
                              : `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}u`)
                          : '—')}
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
