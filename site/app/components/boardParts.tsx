'use client'

/** The small pieces every board draws: a price, a sum of money, a kick-off,
 *  the live clock, and what a price's tooltip says. Shared by the table
 *  (BoardView) and the cards (GameCard), so the two can never write the same
 *  number two ways. */

import { dayTimeText, priceText, timeText, useMounted, type OddsFormat } from '../lib/display'
import type { BoardRow, ShownPrice } from '../lib/boardRow'
import type { BestPick } from '../lib/venues'
import { IconLock } from './icons'

// ── writing numbers ──────────────────────────────────────────────────────────

export function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0.01 || p >= 0.99) return '—'
  return priceText(p, f)
}

/** One board cell: the price, or a lock that says why there is none. */
export function Shown({ s, f }: { s: ShownPrice; f: OddsFormat }) {
  if (s.kind === 'price') return <>{priceText(s.ask, f)}</>
  return (
    <span className="np-closed" role="img" aria-label={s.why} title={s.why}>
      <IconLock />
    </span>
  )
}

export function money(v: number | null | undefined): string {
  if (v == null || v <= 0) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

/** Kick-off in the reader's own zone. */
export function clock(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const mins = Math.round((d.getTime() - Date.now()) / 60000)
  if (mins < 0) return timeText(d)
  if (mins < 60) return `in ${mins}m`
  if (mins < 24 * 60) return `in ${Math.floor(mins / 60)}h ${mins % 60}m`
  return dayTimeText(d)
}

/** How much more a winning bet pays at the cheaper venue, after both fees.
 *  "Pays 2.4% more" is the saving a bettor actually feels; the probability
 *  points it is computed from are not. */
export function paysMorePct(pick: BestPick | undefined): number | null {
  if (!pick || pick.net == null || pick.savingPp == null || pick.net <= 0) return null
  return ((pick.net + pick.savingPp / 100) / pick.net - 1) * 100
}

// ── what the clock is doing ──────────────────────────────────────────────────

export function LiveState({ r }: { r: BoardRow }) {
  const mounted = useMounted()
  if (r.finished) return <span className="np-badge">{r.detail || 'FT'}</span>

  if (r.live) {
    // ESPN's own status string, where we have one: "Q4 0:06" and "Top 7th"
    // are what the sport calls the moment.
    if (r.detail && r.minute == null) {
      return <span className="np-badge is-live">● {r.detail}</span>
    }
    if (r.liveSource === 'pm' || r.liveSource === 'feed') {
      return (
        <span className="np-badge is-live">
          {r.phase === 'HT' ? (
            'Half time'
          ) : (
            <>
              ● {r.minute != null ? <span className="np-num">{r.minute}&apos;</span> : 'Live'}
              {r.phase === 'ET' || r.phase === 'PEN' ? ` ${r.phase}` : ''}
            </>
          )}
        </span>
      )
    }
    if (r.liveSource === 'board') {
      return <span className="np-badge is-live">● Live</span>
    }
    // The listed start has passed and nothing confirms the game is on. Said
    // plainly, without claiming a live clock we do not have.
    return (
      <span
        className="np-badge"
        title="The listed start time has passed, but no live feed covers this game yet."
      >
        Started
      </span>
    )
  }
  // The start time is written in the reader's zone and relative to their
  // clock, neither of which a server render knows.
  return <span className="sc-in np-num">{mounted ? clock(r.kickoff) : ''}</span>
}
