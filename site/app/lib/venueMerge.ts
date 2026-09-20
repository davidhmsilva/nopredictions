/** Kalshi's soccer, laid over Polymarket's board.
 *
 *  🔑 This runs in the BROWSER, not on the server, and that is deliberate.
 *     Polymarket's whole board is one paged Gamma sweep; Kalshi's is 139
 *     separate series requests it rate-limits to about four a second, so a
 *     full sweep takes ~34 seconds. Putting that inside `/api/scout` would
 *     turn a 0.4-second board into a 35-second one for everybody, to add a
 *     column that only some fixtures have.
 *
 *     So the page asks for both at once: the board renders the moment
 *     Polymarket answers, and the Kalshi column fills a beat later when
 *     `/api/venues/soccer` comes back. A Kalshi outage costs the column and
 *     nothing else — the board is never waiting on it.
 *
 *  The merge itself is pure: same inputs, same output, on the server or in the
 *  browser. The Game Center uses it too, on one fixture at a time.
 */

import type { KalshiFixture } from './kalshiSoccerTypes'
import type { ScoutFixture } from './scoutTypes'
import { placeVenue } from './venueMatch'
import { etDateOf } from './etDate'
import { bestFor, combinedVolume, gradeOf, type OutcomeKey, type Quote, type VenueBook } from './venues'

/** The goals line both venues quote, and the one every measured table on this
 *  site is keyed to. */
const OVER_LINE = '2.5'

/** One Kalshi fixture as a venue book on our own outcome vocabulary. */
export function kalshiBook(k: KalshiFixture): VenueBook {
  const quotes: Partial<Record<OutcomeKey, Quote>> = {}
  if (k.legs.home) quotes.home = k.legs.home.quote
  if (k.legs.draw) quotes.draw = k.legs.draw.quote
  if (k.legs.away) quotes.away = k.legs.away.quote
  const over = k.totals[OVER_LINE]
  if (over) quotes.over25 = over.quote
  return {
    venue: 'kalshi',
    url: k.url,
    volume: k.volume,
    // Graded off the 1X2, the ladder both venues quote, so the two grades on a
    // row are answering the same question.
    grade: gradeOf([quotes.home, quotes.draw, quotes.away]),
    quotes,
    source: 'kalshi',
  }
}

export interface MergeResult {
  fixtures: ScoutFixture[]
  /** How many of our fixtures got a Kalshi book. */
  placed: number
  /** Kalshi fixtures we refused to place because the join was not one-to-one.
   *  Counted and shown, never guessed onto a game. */
  dropped: number
}

/** Returns a NEW array; nothing is mutated. A fixture Kalshi does not list
 *  comes back exactly as it went in — Polymarket alone, and the board says
 *  "not listed" rather than dropping the game. */
export function mergeKalshi(fixtures: ScoutFixture[], kalshi: KalshiFixture[]): MergeResult {
  if (kalshi.length === 0) return { fixtures, placed: 0, dropped: 0 }
  // Polymarket publishes an instant; Kalshi files by the Eastern date. Both
  // sides are given the date so the join runs on the discriminator they share.
  const dated = fixtures.map((f) => ({ ...f, etDate: etDateOf(f.kickoff) }))
  const { placed, dropped } = placeVenue(dated, (f) => f.slug, kalshi)
  if (placed.size === 0) return { fixtures, placed: 0, dropped }

  const out = fixtures.map((f) => {
    const k = placed.get(f.slug)
    if (!k) return f
    const venues = [...f.venues.filter((v) => v.venue !== 'kalshi'), kalshiBook(k)]
    return {
      ...f,
      venues,
      best: bestFor(venues),
      volumeCombinedUsd: combinedVolume({ polymarket: f.volumeUsd, kalshi: k.volume }),
    }
  })
  return { fixtures: out, placed: placed.size, dropped }
}
