'use client'

/** A built strategy and its backtest, written for someone who bets rather
 *  than someone who reads p-values — and the Deploy button it ends on.
 *
 *  The order is the order a bettor asks in: what would I have made, what
 *  exactly will the agent do, can I trust it, what did it bet on. The statistics that answer "can I trust it"
 *  are all still here — the interval, the p-value, the closing-line value, the
 *  season table and the notes — folded under "How sure is this?", and the
 *  plain verdict on top is computed FROM them, so folding them hides nothing.
 */

import Link from 'next/link'
import { useState } from 'react'
import { isNbaMarket, isVenueMarket, seasonName, type BacktestStats, type Spec } from '../lib/backtest'
import type { RecentBet } from '../lib/labQuick'
import { dateText, oddsText, useOddsFormat } from '../lib/display'
import { StrategyCard } from '../components/StrategyCard'

export interface LabApiResult {
  ok: boolean
  error?: string
  supported?: boolean
  reason?: string
  suggestion?: string | null
  title?: string
  interpretation?: string | null
  spec?: Spec
  verdict?: { code: string; label: string; detail: string }
  stats?: BacktestStats
  seasons?: { season: number; n: number; wins: number; pnl: number }[]
  monthly?: { month: string; n: number; pnl: number }[]
  recent?: RecentBet[]
  caveats?: string[]
}

type Tone = 'good' | 'warn' | 'flat'

/** What happened to "Deploy" / "Save as draft" on a result. `busy` says which
 *  of the two is in flight; `running` false with a `note` is an agent that was
 *  saved but could not be switched on (a plan limit, a rule the live agent
 *  cannot compute yet). */
export interface Kept {
  id?: number
  running?: boolean
  note?: string | null
  error?: string
  busy?: 'deploy' | 'draft'
}

/** The buttons every built strategy ends on, and what they did. Shared by the
 *  pre-match result and the live rule.
 *
 *  🔑 Deploy is only the loud button when the evidence does not argue with it.
 *     The point of this page is an agent that makes money, so a strategy that
 *     lost over 14 years says so right here and offers changing the idea first.
 *     Deploying it anyway stays one press away: it is paper, and it is theirs. */
export function KeepActions({
  kept,
  onKeep,
  warn,
  blocked,
  lead,
  children,
}: {
  kept: Kept | null
  onKeep: (deploy: boolean) => void
  /** Why history argues against this one. Demotes Deploy to "Deploy anyway". */
  warn?: string | null
  /** Why it cannot run at all. Removes Deploy; a draft can still be kept. */
  blocked?: string | null
  /** A button that belongs before Deploy. */
  lead?: React.ReactNode
  /** Anything else that belongs in the row. */
  children?: React.ReactNode
}) {
  if (kept?.id) {
    return (
      <div className="cr-kept" role="status">
        {kept.running ? (
          <>
            <b>Your agent is deployed.</b> It is running now and places a paper bet on the next game
            that fits. <Link href={`/agent/${kept.id}`}>Open your agent →</Link>
          </>
        ) : (
          <>
            <b>Saved to your agents.</b>{' '}
            {kept.note ? <>It is not running yet: {kept.note} </> : 'Press Run on its page when you want it to start. '}
            <Link href={`/agent/${kept.id}`}>Open your agent →</Link>
          </>
        )}
      </div>
    )
  }
  return (
    <>
      {warn && <p className="cr-warn">{warn}</p>}
      {blocked && <p className="cr-warn">{blocked}</p>}
      <div className="lr-actions">
        {lead}
        {!blocked && (
          <button
            type="button"
            className={`np-btn${warn ? '' : ' np-btn-primary'}`}
            disabled={Boolean(kept?.busy)}
            onClick={() => onKeep(true)}
          >
            {kept?.busy === 'deploy' ? 'Deploying…' : warn ? 'Deploy anyway' : 'Deploy this agent'}
          </button>
        )}
        <button type="button" className="np-btn" disabled={Boolean(kept?.busy)} onClick={() => onKeep(false)}>
          {kept?.busy === 'draft' ? 'Saving…' : 'Save as a draft'}
        </button>
        {children}
      </div>
      {kept?.error && <p className="lr-error">{kept.error}</p>}
      {!blocked && (
        <p className="lr-note">
          Deployed, it paper-trades the next games that fit — practice bets at real prices, settled on
          the result — so you see how it does from today on. History can only say so much.
        </p>
      )}
    </>
  )
}

/** The verdict in a bettor's words. Same inputs as `verdict()` in lib/backtest
 *  plus the closing-line value, which that function does not read — and rule 5
 *  of this project says money made without the price moving your way is
 *  probably luck, so the plain verdict has to. */
export function plainVerdict(s: BacktestStats, code: string, market?: string): { tone: Tone; label: string; line: string } {
  if (market && isVenueMarket(market)) return venueVerdict(s, code)
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

/** The same verdict for a market replayed at Polymarket's own prices. It buys at the
 *  last price before kick-off, so no later price can say whether the market agreed:
 *  a profit here never reads as more than "not proven yet". */
function venueVerdict(s: BacktestStats, code: string): { tone: Tone; label: string; line: string } {
  if (code === 'NO_MATCHES') {
    return { tone: 'flat', label: 'No games fit this', line: 'Nothing in about a year of Polymarket prices matches every condition. Loosen one and try again.' }
  }
  if (code === 'INSUFFICIENT_SAMPLE') {
    return {
      tone: 'flat',
      label: 'A record, not a result',
      line: `${s.n} bets at Polymarket’s prices. Under 200 it is a record, not a result: a good run looks like a pattern for a long time.`,
    }
  }
  if (code === 'EDGE_FOUND') {
    return {
      tone: 'warn',
      label: 'Made money — not proven yet',
      line: 'It made money at Polymarket’s own prices, over about a year. One year is not fourteen, and this buys at the last price before kick-off, so no later price can say whether the market agreed. Worth tracking on new games.',
    }
  }
  if (code === 'SIGNIFICANTLY_NEGATIVE') {
    return {
      tone: 'flat',
      label: 'Loses money, steadily',
      line: 'Over about a year this lost more than the spread and the fee alone would explain. Betting the opposite is not a fix: you pay the spread and the fee on that side too.',
    }
  }
  return s.yieldPct > 0
    ? { tone: 'flat', label: 'Slightly up, within luck', line: 'A small profit, but well inside what chance produces over this many bets.' }
    : { tone: 'flat', label: 'Nothing here beats the price', line: 'Over about a year the result is about what the spread and the fee cost.' }
}

function units(x: number, dp = 1): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(dp)}`
}

const monthYear = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })

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
  kept,
  onKeep,
  onTryAnother,
}: {
  r: LabApiResult
  kept: Kept | null
  onKeep: (deploy: boolean) => void
  onTryAnother: () => void
}) {
  const fmt = useOddsFormat()
  const [open, setOpen] = useState(false)
  const s = r.stats!
  const v = plainVerdict(s, r.verdict!.code, r.spec?.market)
  const venue = r.spec != null && isVenueMarket(r.spec.market)
  const seasons = r.seasons ?? []
  const seasonLabel = (y: number) => seasonName(y, r.spec?.leagues)
  const best = seasons.length ? seasons.reduce((a, b) => (b.pnl > a.pnl ? b : a)) : null
  const from = s.firstMatch ? new Date(s.firstMatch).getUTCFullYear() : null
  const to = s.lastMatch ? new Date(s.lastMatch).getUTCFullYear() : null
  const losses = s.n - s.wins - (s.pushes ?? 0)
  const blocked = r.spec && isNbaMarket(r.spec.market)
    ? 'NBA agents cannot trade yet: the NBA history ends in 2021-22 and there are no NBA boards for this agent to bet on. You can keep it as a draft.'
    : null
  const warn = blocked
    ? null
    : s.n === 0
      ? 'No past game fits this rule, so it may never place a bet. Loosening one condition usually fixes that.'
      : s.yieldPct < 0
        ? 'This lost money over the games it would have bet. An agent can only prove an idea on new games, but this one starts behind: try narrowing it to a league or a price band first.'
        : null
  const change = (
    <button type="button" className={`np-btn${warn ? ' np-btn-primary' : ''}`} onClick={onTryAnother}>
      Change the idea
    </button>
  )

  return (
    <section className="lr" aria-live="polite">
      <div className="lr-title">
        {r.title ?? 'Your idea'}
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

      {venue && (
        <p className="lr-note">
          Replayed over about a year{s.firstMatch && s.lastMatch ? ` (${monthYear(s.firstMatch)} to ${monthYear(s.lastMatch)})` : ''} at
          Polymarket&apos;s own prices, the last price a taker paid before kick-off plus the fee. Not 14 years
          at Pinnacle&apos;s close. Under 200 bets it is a record, not a result.
        </p>
      )}

      {r.spec && <StrategyCard spec={r.spec} label="Your agent's strategy" />}

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

      <KeepActions kept={kept} onKeep={onKeep} warn={warn} blocked={blocked} lead={warn ? change : null}>
        {!warn && change}
        {s.n > 0 && (
          <button type="button" className="np-btn lr-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            How sure is this? <span aria-hidden="true">{open ? '▴' : '▾'}</span>
          </button>
        )}
      </KeepActions>

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
                  ? `Average move from the opening price to the close. Bettors who really have an edge usually see the price shorten after they bet.${s.nOpen < s.n ? ` Measured on the ${s.nOpen.toLocaleString('en-US')} of these bets that have an opening price.` : ''}`
                  : venue
                    ? 'It buys at the last price before kick-off, so there is no later price to compare with.'
                    : 'This dataset has no opening price, so the move cannot be measured.'}
              </p>
            </div>
            {s.midYieldPct != null && (
              <div>
                <dt>The ceiling: the same bets at the mid</dt>
                <dd className="np-num">{units(s.midYieldPct, 2)}%</dd>
                <p>The middle of the book, plus the fee. Nobody buys there, so this is an upper limit and never a result.</p>
              </div>
            )}
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
