'use client'

/** One Lab result, written for someone who bets rather than someone who
 *  reads p-values.
 *
 *  The order is the order a bettor asks in: what would I have made, can I
 *  trust it, what did it bet on. The statistics that answer "can I trust it"
 *  are all still here — the interval, the p-value, the closing-line value, the
 *  season table and the notes — folded under "How sure is this?", and the
 *  plain verdict on top is computed FROM them, so folding them hides nothing.
 */

import Link from 'next/link'
import { useState } from 'react'
import type { BacktestStats } from '../lib/backtest'
import type { RecentBet } from '../lib/labQuick'
import { dateText, oddsText, useOddsFormat } from '../lib/display'

export interface LabApiResult {
  ok: boolean
  error?: string
  supported?: boolean
  reason?: string
  suggestion?: string | null
  title?: string
  interpretation?: string | null
  spec?: { market?: string }
  verdict?: { code: string; label: string; detail: string }
  stats?: BacktestStats
  seasons?: { season: number; n: number; wins: number; pnl: number }[]
  monthly?: { month: string; n: number; pnl: number }[]
  recent?: RecentBet[]
  caveats?: string[]
}

type Tone = 'good' | 'warn' | 'flat'

/** The verdict in a bettor's words. Same inputs as `verdict()` in lib/backtest
 *  plus the closing-line value, which that function does not read — and rule 5
 *  of this project says money made without the price moving your way is
 *  probably luck, so the plain verdict has to. */
export function plainVerdict(s: BacktestStats, code: string): { tone: Tone; label: string; line: string } {
  if (code === 'NO_MATCHES') {
    return { tone: 'flat', label: 'No games fit this', line: 'Nothing in 14 seasons matches every condition. Loosen one and try again.' }
  }
  if (code === 'INSUFFICIENT_SAMPLE') {
    return {
      tone: 'flat',
      label: 'Too few games to tell',
      line: `${s.n} bets is not enough to separate a pattern from a good run. We want 200 before saying anything.`,
    }
  }
  if (code === 'EDGE_FOUND') {
    if (s.clvPct == null) {
      return {
        tone: 'warn',
        label: 'Made money — not proven yet',
        line: 'It beat the closing price over enough games to matter. There is no opening price here to check whether the market agreed.',
      }
    }
    if (s.clvPct <= 0) {
      return {
        tone: 'warn',
        label: 'Made money, but it looks like luck',
        line: 'It finished up, but the odds did not move its way between open and close. When the market never agrees, a profit is usually a kind sample.',
      }
    }
    return {
      tone: 'good',
      label: 'Made money, and the market agreed',
      line: 'It beat the closing price over enough games to matter, and the odds tended to shorten after you would have bet. Worth tracking on new games.',
    }
  }
  if (code === 'SIGNIFICANTLY_NEGATIVE') {
    return {
      tone: 'flat',
      label: 'Loses money, steadily',
      line: 'This loses more than the bookmaker’s margin alone would explain. Betting the opposite is not a fix — the margin cuts both ways.',
    }
  }
  return s.yieldPct > 0
    ? { tone: 'flat', label: 'Slightly up, within luck', line: 'A small profit, but well inside what chance produces over this many bets.' }
    : { tone: 'flat', label: 'The market already prices this', line: 'The result is about what the bookmaker’s margin costs. Nothing here beats the price.' }
}

function units(x: number, dp = 1): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(dp)}`
}

function seasonLabel(y: number): string {
  return `${y}-${String((y + 1) % 100).padStart(2, '0')}`
}

function Curve({ monthly }: { monthly: { month: string; pnl: number }[] }) {
  if (monthly.length < 2) return null
  const W = 640
  const H = 150
  const PAD = 6
  let cum = 0
  const pts = monthly.map((m) => (cum += m.pnl))
  const min = Math.min(0, ...pts)
  const max = Math.max(0, ...pts)
  const range = max - min || 1
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => PAD + (1 - (v - min) / range) * (H - 2 * PAD)
  const line = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`
  return (
    <figure className="lr-curve">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Running profit, month by month">
        <path d={area} className="lr-curve-area" />
        <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} className="lr-curve-zero" />
        <path d={line} className="lr-curve-line" />
      </svg>
      <figcaption>
        <span>{monthly[0].month.slice(0, 4)}</span>
        <span>Running profit, 1 unit a bet</span>
        <span>{monthly[monthly.length - 1].month.slice(0, 4)}</span>
      </figcaption>
    </figure>
  )
}

export function LabResult({
  r,
  saved,
  onSave,
  onTryAnother,
}: {
  r: LabApiResult
  saved: { id?: number; error?: string; busy?: boolean } | null
  onSave: () => void
  onTryAnother: () => void
}) {
  const fmt = useOddsFormat()
  const [open, setOpen] = useState(false)
  const s = r.stats!
  const v = plainVerdict(s, r.verdict!.code)
  const seasons = r.seasons ?? []
  const best = seasons.length ? seasons.reduce((a, b) => (b.pnl > a.pnl ? b : a)) : null
  const from = s.firstMatch ? new Date(s.firstMatch).getUTCFullYear() : null
  const to = s.lastMatch ? new Date(s.lastMatch).getUTCFullYear() : null
  const losses = s.n - s.wins - (s.pushes ?? 0)

  return (
    <section className="lr" aria-live="polite">
      <div className="lr-title">
        {r.title ?? 'Your theory'}
        {s.n > 0 && (
          <span>
            {' '}
            · {s.n.toLocaleString('en-US')} bets{from && to ? ` · ${from}–${to}` : ''}
          </span>
        )}
      </div>

      {s.n > 0 ? (
        <h2 className="lr-head">
          {s.pnl >= 0 ? 'Would have made ' : 'Would have lost '}
          <b className="np-num">{Math.abs(s.pnl).toFixed(1)} units</b>
          <span className="lr-head-sub np-num"> ({units(s.yieldPct, 1)}% a bet)</span>
        </h2>
      ) : (
        <h2 className="lr-head">No games fit this</h2>
      )}

      <div className={`lr-verdict is-${v.tone}`}>
        <strong>{v.label}</strong>
        <span>{v.line}</span>
      </div>

      {s.n > 0 && (
        <>
          <Curve monthly={r.monthly ?? []} />

          <dl className="lr-facts">
            <div>
              <dt>Record</dt>
              <dd className="np-num">
                {s.wins.toLocaleString('en-US')} won · {losses.toLocaleString('en-US')} lost
              </dd>
            </div>
            <div>
              <dt>Won</dt>
              <dd className="np-num">{s.hitRatePct.toFixed(1)}%</dd>
            </div>
            <div>
              <dt>Average odds</dt>
              <dd className="np-num">{oddsText(s.avgOdds, fmt)}</dd>
            </div>
            {best && seasons.length > 1 && (
              <div>
                <dt>Best season</dt>
                <dd className="np-num">
                  {seasonLabel(best.season)} · {units(best.pnl)}
                </dd>
              </div>
            )}
            <div>
              <dt>Worst run</dt>
              <dd className="np-num">−{s.maxDrawdown.toFixed(1)} units</dd>
            </div>
          </dl>

          {(r.recent?.length ?? 0) > 0 && (
            <div className="lr-recent">
              <h3>The last bets it would have made</h3>
              <ul>
                {r.recent!.map((b, i) => (
                  <li key={i}>
                    <span className="lr-recent-when">{dateText(new Date(b.kickoff), true)}</span>
                    <span className="lr-recent-game">
                      {b.home}{' '}
                      <b className="np-num">
                        {b.home_score ?? '–'}–{b.away_score ?? '–'}
                      </b>{' '}
                      {b.away}
                      <em>{b.league}</em>
                    </span>
                    <span className="lr-recent-odds np-num">{oddsText(b.odds, fmt)}</span>
                    <span className={`lr-recent-res np-num${b.won ? ' is-won' : ''}`}>
                      {b.won ? `Won ${units(b.pnl, 2)}` : 'Lost −1'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="lr-actions">
        {saved?.id ? (
          <p className="lr-saved">
            Saved. <Link href={`/agent/${saved.id}`}>Open it in Agents</Link> and press Run — it bets
            nothing until you do.
          </p>
        ) : (
          <button type="button" className="np-btn np-btn-primary" disabled={saved?.busy} onClick={onSave}>
            {saved?.busy ? 'Saving…' : 'Track it on new games'}
          </button>
        )}
        <button type="button" className="np-btn" onClick={onTryAnother}>
          Try another
        </button>
        {s.n > 0 && (
          <button type="button" className="np-btn lr-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            How sure is this? <span aria-hidden="true">{open ? '▴' : '▾'}</span>
          </button>
        )}
      </div>
      {saved?.error && <p className="lr-error">{saved.error}</p>}
      {!saved?.id && (
        <p className="lr-note">
          Tracking paper-trades the next games that fit, so you see how it does from today on — history
          can only say so much.
        </p>
      )}

      {open && s.n > 0 && (
        <div className="lr-detail">
          {r.interpretation && (
            <div className="lr-block">
              <h3>What was tested</h3>
              <p>{r.interpretation}</p>
            </div>
          )}

          <dl className="lr-stats">
            <div>
              <dt>Return per bet, with its range</dt>
              <dd className="np-num">
                {units(s.yieldPct, 2)}% ± {s.ci95Pct.toFixed(2)}
              </dd>
              <p>95 times in 100, the true return sits inside this range. If the range crosses zero, so might the strategy.</p>
            </div>
            <div>
              <dt>How often luck alone does this</dt>
              <dd className="np-num">{s.pValue != null ? `${(s.pValue * 100).toFixed(1)}%` : '—'}</dd>
              <p>How often a strategy with no edge at all would land this far from zero (the p-value). Under 5% is the usual bar.</p>
            </div>
            <div>
              <dt>Did the odds move its way?</dt>
              <dd className="np-num">{s.clvPct != null ? `${units(s.clvPct, 2)}%` : 'Not measured'}</dd>
              <p>
                {s.clvPct != null
                  ? 'Average move from the opening price to the close. Bettors who really have an edge usually see the price shorten after they bet.'
                  : 'This dataset has no opening price, so the move cannot be measured.'}
              </p>
            </div>
          </dl>

          {seasons.length > 1 && (
            <div className="lr-block">
              <h3>Season by season</h3>
              <div className="lr-table-wrap">
                <table className="lr-table">
                  <thead>
                    <tr>
                      <th>Season</th>
                      <th className="num">Bets</th>
                      <th className="num">Won</th>
                      <th className="num">Profit</th>
                      <th className="num">Per bet</th>
                    </tr>
                  </thead>
                  <tbody>
                    {seasons.map((row) => (
                      <tr key={row.season}>
                        <td>{seasonLabel(row.season)}</td>
                        <td className="num np-num">{row.n.toLocaleString('en-US')}</td>
                        <td className="num np-num">{((row.wins / row.n) * 100).toFixed(0)}%</td>
                        <td className="num np-num">{units(row.pnl)}</td>
                        <td className="num np-num">{units((row.pnl / row.n) * 100)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(r.caveats?.length ?? 0) > 0 && (
            <div className="lr-block">
              <h3>Worth knowing</h3>
              <ul className="lr-notes">
                {r.caveats!.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
