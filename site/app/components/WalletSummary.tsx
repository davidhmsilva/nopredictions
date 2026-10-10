'use client'

/** The top of a wallet report: who it is, four numbers, how they trade in a
 *  few plain sentences, whether it holds up, and their latest bets.
 *
 *  Everything here is read off the same profile the full analysis renders —
 *  no new measurement. Each sentence below is a threshold on a number in the
 *  profile, the same rule `narrate()` in lib/wallet follows, written for a
 *  bettor instead of for the research log.
 */

import type { RecentPosition, WalletProfile } from '../lib/wallet'

function money(x: number): string {
  const a = Math.abs(x)
  const sign = x < 0 ? '−' : ''
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`
  if (a >= 1e4) return `${sign}$${(a / 1e3).toFixed(0)}K`
  if (a >= 1000) return `${sign}$${a.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  return `${sign}$${a.toFixed(a >= 100 ? 0 : 2)}`
}

function dur(min: number): string {
  if (min < 1) return 'under a minute'
  if (min < 60) return `${Math.round(min)} minute${Math.round(min) === 1 ? '' : 's'}`
  if (min < 1440) return `${(min / 60).toFixed(min < 600 ? 1 : 0)} hours`
  return `${(min / 1440).toFixed(1)} days`
}

/** A share price as Polymarket shows it: 93¢, and 0.4¢ rather than a false 0¢. */
function cents(p: number): string {
  const c = p * 100
  return c < 1 ? `${c.toFixed(1)}¢` : `${Math.round(c)}¢`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** From a table, not toLocaleDateString — Node and Chrome disagree ("Sep" / "Sept"). */
function monthYear(ts: number): string {
  const d = new Date(ts * 1000)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}
function dayMonth(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`
}

/** The research archetypes, renamed for someone who bets. */
const STYLE: Record<string, string> = {
  sweeper: 'Buys outcomes already decided',
  inplay_scalper: 'In and out during matches',
  prematch: 'Bets before kick-off',
  post_whistle: 'Buys after the final whistle',
  longshot: 'Backs longshots',
  favourite_grinder: 'Backs near-certainties',
  maker: 'Paid to post orders',
  concentrated: 'A few big bets',
  systematic: 'Runs like a bot',
}

type Tone = 'good' | 'warn' | 'flat'

/** Skill or luck, from the bootstrap clustered by event — resampling GAMES,
 *  so one lucky tournament cannot carry the verdict. */
export function skillVerdict(p: WalletProfile): { tone: Tone; label: string; line: string } {
  const b = p.bootstrap
  const pct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(1)}%`
  if (b.ci_lo === null || b.ci_hi === null) {
    return { tone: 'flat', label: 'Too few games to judge', line: `Only ${b.events} games — not enough to separate skill from a run.` }
  }
  const range = `between ${pct(b.ci_lo)} and ${pct(b.ci_hi)}`
  if (b.ci_lo > 0) {
    return {
      tone: 'good',
      label: 'Holds up — not one lucky run',
      line: `Reshuffled game by game over ${b.events.toLocaleString('en-US')} games, the return stays above zero: ${range}.`,
    }
  }
  if (b.ci_hi < 0) {
    return {
      tone: 'flat',
      label: 'Losing, and not by bad luck',
      line: `Reshuffled game by game over ${b.events.toLocaleString('en-US')} games, the return stays below zero: ${range}.`,
    }
  }
  return {
    tone: 'warn',
    label: p.totals.pnl >= 0 ? 'Up, but it could be luck' : 'Down, but it could be bad luck',
    line: `Reshuffled game by game over ${b.events.toLocaleString('en-US')} games, the return could be anywhere ${range}. Zero is inside that.`,
  }
}

/** How they trade, in sentences. Each one fires on a number, or is not said. */
export function plainReading(p: WalletProfile): string[] {
  const t = p.totals
  const tim = p.timing
  const out: string[] = []
  const live = tim.in_match.share_of_cost + tim.whistle.share_of_cost + tim.settle.share_of_cost
  const pre = tim.prematch.share_of_cost
  const when =
    live >= 80
      ? 'Bets almost entirely during matches'
      : pre >= 60
        ? 'Bets mostly before kick-off'
        : 'Bets both before and during matches'
  const hold = p.hold.median_min
  out.push(`${when}, and usually holds a position for ${dur(hold)}. A typical bet is ${money(t.median_ticket)}.`)

  const fb = p.sport?.football_pct ?? 0
  if (fb >= 80) out.push('Almost all of it is football.')
  else if (fb >= 40) out.push(`About ${Math.round(fb)}% of the money goes on football.`)
  else if (fb > 0) out.push('Mostly sports other than football.')

  if (t.pnl > 0) {
    const sw = p.sweeps
    const after = tim.whistle.pnl + tim.settle.pnl
    if (sw.lots >= 20 && sw.share_of_pnl >= 25) {
      out.push(
        `${Math.round(Math.min(sw.share_of_pnl, 100))}% of the profit comes from buying outcomes that were already settled, at a few cents, before the market caught up.`,
      )
    } else if (after > 0 && (100 * after) / t.pnl >= 30) {
      out.push(`${Math.round(Math.min((100 * after) / t.pnl, 100))}% of the profit is made after the final whistle.`)
    }
    if (p.concentration.top5_pct >= 60) {
      out.push(
        `The best 5 games made ${Math.round(Math.min(p.concentration.top5_pct, 100))}% of the profit — a few big wins rather than a steady process.`,
      )
    }
  }
  return out
}

function status(r: RecentPosition): string {
  if (r.status === 'open') return 'Open'
  if (r.status === 'sold') return 'Cashed out'
  return r.status === 'won' ? 'Won' : 'Lost'
}

export function WalletSummary({ p }: { p: WalletProfile }) {
  const t = p.totals
  const cov = p.coverage
  const name = p.name || p.pseudonym || `${p.wallet.slice(0, 6)}…${p.wallet.slice(-4)}`
  const v = skillVerdict(p)
  const styles = p.archetypes.filter((a) => STYLE[a.key])
  const fb = p.sport?.football_pct ?? 0

  return (
    <section className="ws">
      <header className="ws-head">
        {p.profile_image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={p.profile_image} alt="" className="wx-avatar ws-avatar" />
        ) : (
          <span className="wx-avatar is-blank ws-avatar" aria-hidden="true">
            {(name.replace(/^0x/i, '')[0] || '?').toUpperCase()}
          </span>
        )}
        <div>
          <h1 className="ws-name">{name}</h1>
          <div className="ws-meta">
            Trading since {monthYear(cov.first_ts)} · {cov.events.toLocaleString('en-US')} games
            {fb >= 40 ? ` · ${Math.round(fb)}% football` : ''} ·{' '}
            <a href={`https://polymarket.com/profile/${p.wallet}`} target="_blank" rel="noreferrer">
              Polymarket profile ↗
            </a>
          </div>
        </div>
      </header>

      {p.trust !== 'ok' && (
        <div className="tp-warn ws-trust">
          {p.trust === 'partial'
            ? `We could only read this account back to ${monthYear(cov.first_ts)} — it trades too much to read in one go. The numbers cover that window, not the whole record.`
            : 'Our rebuild does not match Polymarket’s own profit figure for this wallet, so treat the totals as rough. The full analysis says why.'}
        </div>
      )}

      <dl className="ws-nums">
        <div>
          <dt>Profit</dt>
          <dd className="np-num">
            {t.pnl >= 0 ? '+' : ''}
            {money(t.pnl)}
          </dd>
          <span>after fees</span>
        </div>
        <div>
          <dt>Return</dt>
          <dd className="np-num">
            {t.yield_pct >= 0 ? '+' : '−'}
            {Math.abs(t.yield_pct).toFixed(1)}%
          </dd>
          <span>on {money(t.deployed)} staked</span>
        </div>
        <div>
          <dt>Games won</dt>
          <dd className="np-num">{Math.round(p.concentration.profitable_events_pct)}%</dd>
          <span>of {cov.events.toLocaleString('en-US')}</span>
        </div>
        <div>
          <dt>Typical bet</dt>
          <dd className="np-num">{money(t.median_ticket)}</dd>
          <span>biggest {money(t.max_ticket)}</span>
        </div>
      </dl>

      <div className={`lr-verdict is-${v.tone}`}>
        <strong>{v.label}</strong>
        <span>{v.line}</span>
      </div>

      {styles.length > 0 && (
        <div className="ws-styles">
          {styles.map((a) => (
            <span key={a.key} className="ws-style">
              {STYLE[a.key]}
            </span>
          ))}
        </div>
      )}

      <div className="ws-reading">
        {plainReading(p).map((s, i) => (
          <p key={i}>{s}</p>
        ))}
      </div>

      {(p.recent?.length ?? 0) > 0 && (
        <div className="lr-recent ws-recent">
          <h3>Latest bets</h3>
          <ul>
            {p.recent.map((r, i) => (
              <li key={i}>
                <span className="lr-recent-when">{dayMonth(r.ts)}</span>
                <span className="lr-recent-game">
                  {r.event ? (
                    <a href={`https://polymarket.com/event/${r.event}`} target="_blank" rel="noreferrer">
                      {r.title}
                    </a>
                  ) : (
                    r.title
                  )}
                  <em>
                    {r.outcome} · {money(r.cost)} at {cents(r.entry)}
                  </em>
                </span>
                <span className={`lr-recent-res np-num${r.pnl > 0 ? ' is-won' : ''}`}>
                  {status(r)} {r.pnl >= 0 ? '+' : ''}
                  {money(r.pnl)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
