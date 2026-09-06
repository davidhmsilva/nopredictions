'use client'

import { useMemo } from 'react'
import type { PaperTrade } from '../lib/supabase'

/** The agent's record as a line: cumulative units, settled trades only, in the
 *  order they resolved.
 *
 *  A track record is the one thing on this site that has to be a picture. The
 *  page carried the same information as four numbers and a list, which is the
 *  form in which nobody notices a six-week drawdown.
 *
 *  ⚠️ `payout_units` is GROSS. Polymarket's taker fee averages about 1.18pp of
 *  a position and is deducted nowhere in this data, so the curve runs above the
 *  real one by roughly 2.5% of stake per settled trade. The caption says so.
 */
export function EquityCurve({ trades }: { trades: PaperTrade[] }) {
  const model = useMemo(() => {
    const settled = trades
      .filter((t) => !!t.resolved_at)
      .slice()
      .sort((a, b) => new Date(a.resolved_at!).getTime() - new Date(b.resolved_at!).getTime())

    if (settled.length < 2) return null

    let cum = 0
    const pts = settled.map((t) => {
      cum += Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0)
      return { t: new Date(t.resolved_at!).getTime(), v: cum }
    })

    const W = 900
    const H = 190
    const PAD_L = 42
    const PAD_R = 10
    const PAD_T = 12
    const PAD_B = 20

    const vs = pts.map((p) => p.v)
    const lo = Math.min(0, ...vs)
    const hi = Math.max(0, ...vs)
    const span = hi - lo || 1
    const t0 = pts[0].t
    const t1 = pts[pts.length - 1].t

    const x = (t: number) => PAD_L + ((t - t0) / (t1 - t0 || 1)) * (W - PAD_L - PAD_R)
    const y = (v: number) => PAD_T + (1 - (v - lo) / span) * (H - PAD_T - PAD_B)

    const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
    const area =
      `${line} L${x(pts[pts.length - 1].t).toFixed(1)},${y(0).toFixed(1)}` +
      ` L${x(pts[0].t).toFixed(1)},${y(0).toFixed(1)} Z`

    // The deepest peak-to-trough on the curve. A record without this number
    // reads as a slope; with it, it reads as something you would have had to sit
    // through.
    let peak = -Infinity
    let drawdown = 0
    for (const p of pts) {
      peak = Math.max(peak, p.v)
      drawdown = Math.min(drawdown, p.v - peak)
    }

    const wins = settled.filter((t) => t.result === 'won').length
    const staked = settled.reduce((s, t) => s + Number(t.stake_units ?? 0), 0)

    return {
      W, H, PAD_L, line, area, zeroY: y(0),
      final: cum,
      n: settled.length,
      wins,
      losses: settled.length - wins,
      yieldPct: staked > 0 ? (cum / staked) * 100 : 0,
      drawdown,
      from: new Date(t0),
      to: new Date(t1),
      up: cum >= 0,
    }
  }, [trades])

  if (!model) return null

  const dateFmt = (d: Date) => d.toLocaleDateString([], { month: 'short', day: 'numeric' })

  return (
    <section className="eq">
      <div className="eq-stats">
        <div className="eq-stat">
          <span className={`eq-stat-v np-num ${model.up ? 'is-up' : 'is-down'}`}>
            {model.final >= 0 ? '+' : ''}
            {model.final.toFixed(1)}u
          </span>
          <span className="eq-stat-k">Cumulative P&amp;L</span>
        </div>
        <div className="eq-stat">
          <span className={`eq-stat-v np-num ${model.yieldPct >= 0 ? 'is-up' : 'is-down'}`}>
            {model.yieldPct >= 0 ? '+' : ''}
            {model.yieldPct.toFixed(1)}%
          </span>
          <span className="eq-stat-k">Yield on turnover</span>
        </div>
        <div className="eq-stat">
          <span className="eq-stat-v np-num">
            {model.wins}<i>/</i>{model.losses}
          </span>
          <span className="eq-stat-k">Won / lost</span>
        </div>
        <div className="eq-stat">
          <span className="eq-stat-v np-num is-down">{model.drawdown.toFixed(1)}u</span>
          <span className="eq-stat-k">Worst drawdown</span>
        </div>
        <div className="eq-stat">
          <span className="eq-stat-v np-num">{model.n}</span>
          <span className="eq-stat-k">Settled bets</span>
        </div>
      </div>

      <div className="eq-chart">
        <svg
          viewBox={`0 0 ${model.W} ${model.H}`}
          className="eq-svg"
          role="img"
          aria-label="Cumulative profit and loss in units across every settled paper trade"
        >
          <defs>
            <linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={model.up ? '#22c55e' : '#ef4444'} stopOpacity="0.22" />
              <stop offset="100%" stopColor={model.up ? '#22c55e' : '#ef4444'} stopOpacity="0" />
            </linearGradient>
          </defs>
          <line x1={model.PAD_L} y1={model.zeroY} x2={model.W - 10} y2={model.zeroY} className="eq-zero" />
          <text x={6} y={model.zeroY + 5} className="eq-tick">0u</text>
          <path d={model.area} fill="url(#eq-fill)" />
          <path
            d={model.line}
            fill="none"
            stroke={model.up ? '#22c55e' : '#ef4444'}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </svg>
        <div className="eq-axis">
          <span>{dateFmt(model.from)}</span>
          <span>{dateFmt(model.to)}</span>
        </div>
      </div>

      <p className="eq-note">
        Every settled paper bet, one unit each, in the order it resolved.{' '}
        <strong>Gross of the Polymarket taker fee</strong>, which averages about 1.18 points of a
        position — the real line sits below this one.
      </p>
    </section>
  )
}
