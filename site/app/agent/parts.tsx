'use client'

/** What the agent list and the agent page share: numbers written one way, the
 *  status badge, and the two curves. Client-side because every date here is in
 *  the reader's own zone (lib/display), and the server has no reader. */

import type { AgentSummary, AgentTrade } from '../lib/agents'
import { dateText } from '../lib/display'

// ── numbers ─────────────────────────────────────────────────────────────────

export const units = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(2)}u`
export const pct = (n: number, digits = 1) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(digits)}%`
export const tone = (n: number | null | undefined) => (n == null || n === 0 ? '' : n > 0 ? 'ag-pos' : 'ag-neg')

export function settledOf(a: AgentSummary): number {
  return a.wins + a.losses
}

/** "3h ago", "2d ago", then a date. A record that stopped a month ago should
 *  say when, not "34d ago". */
export function ago(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const mins = (Date.now() - d.getTime()) / 60_000
  if (mins < 60) return `${Math.max(1, Math.round(mins))}m ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`
  if (mins < 60 * 24 * 14) return `${Math.round(mins / 1440)}d ago`
  return dateText(d, d.getFullYear() !== new Date().getFullYear())
}

/** "Since 11 Sep" — or, before its first bet, when it was made. */
export function sinceText(a: AgentSummary): string {
  if (a.first_bet_at) {
    const d = new Date(a.first_bet_at)
    return `Since ${dateText(d, d.getFullYear() !== new Date().getFullYear())}`
  }
  return 'No bets yet'
}

// ── status ──────────────────────────────────────────────────────────────────

const STATUS: Record<AgentSummary['run_status'], { label: string; cls: string }> = {
  running: { label: 'RUNNING', cls: 'is-good' },
  paused: { label: 'PAUSED', cls: '' },
  draft: { label: 'NOT RUNNING', cls: '' },
}

export function StatusBadge({ a }: { a: AgentSummary }) {
  const s = STATUS[a.run_status]
  return <span className={`np-badge ${s.cls}`}>{s.label}</span>
}

// ── a trade's pick and its event ────────────────────────────────────────────

const HUMAN: Record<string, string> = {
  btts: 'Both teams to score',
  ht_home_win: 'Home leading at HT', ht_away_win: 'Away leading at HT', ht_draw: 'Level at HT',
}

/** The older agents wrote machine keys — "away_win", "home_wins_by_2plus".
 *  Anything shaped like one is read out as words; anything else is left as
 *  written, because it already is words ("Cavalry FC win", "Over"). */
function human(key: string): string {
  const k = key.toLowerCase()
  if (HUMAN[k]) return HUMAN[k]
  if (!/^[a-z0-9_]+$/.test(key)) return key
  const words = k.replace(/(\d)plus\b/g, '$1+').replace(/\bht\b/g, 'HT').replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The in-play arms and the NFL agent write "Over 4.5 — Home vs. Away" into
 *  the outcome and leave the market title empty; the older agents write a
 *  side ("home", "yes") and put the fixture in the market title. Both end up
 *  as the same two lines: what was bought, and on what. */
export function pickOf(t: AgentTrade): { pick: string; event: string | null } {
  const i = t.pick.indexOf(' — ')
  if (i > 0) return { pick: t.pick.slice(0, i), event: t.pick.slice(i + 3) }
  const pick = human(t.pick)
  return { pick, event: t.event && t.event !== '—' ? t.event : null }
}

// ── curves ──────────────────────────────────────────────────────────────────

function path(values: number[], w: number, h: number, pad: number, lo: number, hi: number): string {
  const span = hi - lo || 1
  const x = (i: number) => (values.length === 1 ? w / 2 : (i / (values.length - 1)) * w)
  const y = (v: number) => pad + (1 - (v - lo) / span) * (h - 2 * pad)
  return values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
}

/** The card's curve: P&L after each settled bet, from zero. Green if it ends
 *  above where it started, red if below — the same test as the P&L tile. */
export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="ag-spark-none">{values.length ? 'One settled bet' : 'No settled bets yet'}</div>
  const series = [0, ...values]
  const lo = Math.min(0, ...series)
  const hi = Math.max(0, ...series)
  const W = 240
  const H = 44
  const PAD = 3
  const end = series[series.length - 1]
  const zeroY = PAD + (1 - (0 - lo) / (hi - lo || 1)) * (H - 2 * PAD)
  return (
    <svg className="ag-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <line className="ag-spark-zero" x1="0" x2={W} y1={zeroY} y2={zeroY} vectorEffect="non-scaling-stroke" />
      <path
        d={path(series, W, H, PAD, lo, hi)}
        fill="none"
        stroke={end >= 0 ? 'var(--green)' : 'var(--red)'}
        strokeWidth="1.6"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** The agent page's curve, with its scale: the high, zero and the low on the
 *  left, the first and last settlement dates underneath. */
export function EquityChart({ curve }: { curve: { t: string; pl: number }[] }) {
  if (curve.length < 2) {
    return <p className="gc-quiet">The curve starts after the second settled bet.</p>
  }
  const series = [0, ...curve.map((c) => c.pl)]
  const lo = Math.min(0, ...series)
  const hi = Math.max(0, ...series)
  const W = 1000
  const H = 240
  const PAD = 14
  const LEFT = 64
  const plotW = W - LEFT - 8
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * PAD)
  const line = path(series, plotW, H, PAD, lo, hi)
  const end = series[series.length - 1]
  const stroke = end >= 0 ? 'var(--green)' : 'var(--red)'
  const first = new Date(curve[0].t)
  const last = new Date(curve[curve.length - 1].t)
  const withYear = first.getFullYear() !== last.getFullYear()
  const ticks = Array.from(new Set([hi, 0, lo].map((v) => Number(v.toFixed(2)))))
  return (
    <div className="gc-chart">
      <svg className="gc-chart-svg" viewBox={`0 0 ${W} ${H + 26}`} role="img"
        aria-label={`Profit and loss over ${curve.length} settled bets, ending at ${units(end)}`}>
        {ticks.map((v) => (
          <g key={v}>
            <line className={v === 0 ? 'ag-chart-zero' : 'gc-chart-grid'} x1={LEFT} x2={W - 8} y1={y(v)} y2={y(v)} />
            <text className="gc-chart-tick" x={LEFT - 8} y={y(v) + 4} textAnchor="end">{units(v)}</text>
          </g>
        ))}
        <g transform={`translate(${LEFT},0)`}>
          <path d={`${line} L${plotW},${y(0)} L0,${y(0)} Z`} fill={stroke} opacity="0.08" />
          <path d={line} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        </g>
        <text className="gc-chart-tick" x={LEFT} y={H + 20}>{dateText(first, withYear)}</text>
        <text className="gc-chart-tick" x={W - 8} y={H + 20} textAnchor="end">{dateText(last, withYear)}</text>
      </svg>
    </div>
  )
}
