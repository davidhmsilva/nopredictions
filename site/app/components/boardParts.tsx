'use client'

/** The small pieces every board draws: a price, a sum of money, a kick-off,
 *  the live clock, and what a price's tooltip says. Shared by the table
 *  (BoardView) and the cards (GameCard), so the two can never write the same
 *  number two ways. */

import { dayTimeText, priceText, timeText, type OddsFormat } from '../lib/display'
import type { BoardRow } from '../lib/boardRow'
import { VENUE_NAME, gradeOf, type BestPick, type OutcomeKey, type VenueBook } from '../lib/venues'

// ── writing numbers ──────────────────────────────────────────────────────────

export function odds(p: number | null | undefined, f: OddsFormat): string {
  if (p == null || p <= 0.01 || p >= 0.99) return '—'
  return priceText(p, f)
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
  return <span className="sc-in np-num">{clock(r.kickoff)}</span>
}

// ── one price, across both exchanges ─────────────────────────────────────────

/** The hover text on a price: what each app charges, and which pays more. */
export function pickTitle(
  pick: BestPick,
  venues: VenueBook[],
  key: OutcomeKey,
  f: OddsFormat
): string {
  const lines = venues.map((b) => {
    const q = b.quotes[key]
    if (!q || q.ask == null) return `${VENUE_NAME[b.venue]}: not offered`
    // Graded on THIS leg — the same gate the pick itself uses.
    const grade = gradeOf([q])
    return (
      `${VENUE_NAME[b.venue]}: ${priceText(q.ask, f)}` +
      (grade === 'none' ? ' (no real market behind it, so it cannot count)' : '')
    )
  })
  const more = paysMorePct(pick)
  if (pick.venue && more != null) {
    lines.push(`Pays ${more.toFixed(1)}% more on ${VENUE_NAME[pick.venue]}, after both apps' fees.`)
  } else if (pick.quoted === 1) {
    lines.push('Only one app offers this right now.')
  } else if (pick.quoted > 1) {
    lines.push('Same price on both apps, after fees.')
  }
  return lines.join('\n')
}
