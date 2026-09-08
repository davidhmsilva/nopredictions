/** Dropping odds — the board of what the market moved on.
 *
 *  Derived entirely from the sweep Scout already does. Gamma publishes
 *  `oneDayPriceChange` and `oneHourPriceChange` on every market in the listing
 *  response, so this page costs **no extra request** — the same shape as the
 *  live block that was sitting in those bytes all along.
 *
 *  Three rules decide what appears, and each one is here because of something
 *  measured on 2026-09-08 rather than because it sounded prudent:
 *
 *  1. **Volume floor.** Without one, the biggest movers are internationals
 *     with $5,324 through them showing a 39pp swing — that is not a market
 *     repricing, it is two people. At $1,000 the board goes from 121 fixtures
 *     to 41, of which 16 have a real move.
 *
 *  2. **Pre-match only.** A price moving during a live match is mostly the
 *     score, which the Scout board already shows. Dropping odds is about the
 *     market changing its mind before anyone kicks a ball.
 *
 *  3. **A move worth printing.** Under 2pp is inside the spread on most of
 *     these books.
 *
 *  ⚠️ On the window. SteamWatch shows Friday movers on a Tuesday because it
 *     reads Pinnacle, which prices a week out. This board reads Polymarket,
 *     and Polymarket does not list football that early: measured across a full
 *     1,200-event sweep, the horizon to kickoff runs **median 25h, maximum
 *     52h**. So a four-day-out mover essentially cannot appear here — but the
 *     card still prints the time to kickoff, because "moved 8pp" means
 *     something different at T-40h than at T-2h and the reader should not have
 *     to work out which they are looking at.
 */

import type { ScoutFixture } from './scout'

/** Below this, a "mover" is two people rather than a market. */
export const MIN_VOLUME_USD = 1_000

/** The line between the main board and the thin one.
 *
 *  🔑 Measured on a live board, 2026-09-08. The move distribution is the SAME
 *     in every volume band — median 3.0pp at $1-5k, 3.0pp at $5-10k, 2.5pp at
 *     $10-50k. What differs is the tail: the thin band held a 23.0pp swing on
 *     $2,672 and a 10.5pp on $1,827, while nothing above $5,000 moved more
 *     than 4.0pp all day.
 *
 *     Same median, fat tail on one side only, is the signature of noise rather
 *     than information. So thin books are not hidden — they are separated, and
 *     they do not get to lead a board ranked by move size. Ranking them
 *     together is exactly the thing worth criticising in the tracker this page
 *     was modelled on: its top card was a 39pp move on $5,324. */
export const FUNDED_VOLUME_USD = 5_000

/** Below this, the move is inside the spread. */
export const MIN_MOVE_PP = 2

export interface Mover extends ScoutFixture {
  /** Non-null by construction — `movers()` drops fixtures without one. */
  move: NonNullable<ScoutFixture['move']>
  /** Hours until kickoff. Negative should not occur here; see `movers()`. */
  hoursToKickoff: number | null
}

function hoursTo(iso: string | null): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : (t - Date.now()) / 3_600_000
}

/** Movers on a funded book, and movers on a thin one, kept apart. */
export interface MoverBoard {
  funded: Mover[]
  thin: Mover[]
}

export function moverBoard(fixtures: ScoutFixture[]): MoverBoard {
  const all = movers(fixtures)
  return {
    funded: all.filter((m) => m.volumeUsd >= FUNDED_VOLUME_USD),
    thin: all.filter((m) => m.volumeUsd < FUNDED_VOLUME_USD),
  }
}

export function movers(fixtures: ScoutFixture[]): Mover[] {
  return fixtures
    .filter((f) => {
      if (f.live || f.finished) return false
      if (!f.move || f.move.pp < MIN_MOVE_PP) return false
      if (f.volumeUsd < MIN_VOLUME_USD) return false
      // A kickoff we do not know is a kickoff we cannot caveat, and this board
      // is entirely about when the move happened relative to it.
      const h = hoursTo(f.kickoff)
      return h != null && h > 0
    })
    .map((f) => ({ ...f, move: f.move!, hoursToKickoff: hoursTo(f.kickoff) }))
    .sort((a, b) => b.move.pp - a.move.pp)
}

/** What the board says about itself, so the page can state its own filters
 *  rather than hiding them. */
export interface MoversMeta {
  shown: number
  /** Fixtures that were pre-match and priced, before the filters ran. */
  candidates: number
  droppedForVolume: number
  droppedForSmallMove: number
  /** How many pre-match fixtures Gamma published no 24h change for. Worth
   *  showing: it is a third of the board and it is absence, not stillness. */
  noChangePublished: number
  minVolumeUsd: number
  fundedVolumeUsd: number
  minMovePp: number
}

export function moversMeta(fixtures: ScoutFixture[], shown: number): MoversMeta {
  const pre = fixtures.filter((f) => {
    const h = hoursTo(f.kickoff)
    return !f.live && !f.finished && h != null && h > 0
  })
  const withMove = pre.filter((f) => f.move != null)
  return {
    shown,
    candidates: pre.length,
    droppedForVolume: withMove.filter(
      (f) => f.move!.pp >= MIN_MOVE_PP && f.volumeUsd < MIN_VOLUME_USD
    ).length,
    droppedForSmallMove: withMove.filter(
      (f) => f.volumeUsd >= MIN_VOLUME_USD && f.move!.pp < MIN_MOVE_PP
    ).length,
    noChangePublished: pre.length - withMove.length,
    minVolumeUsd: MIN_VOLUME_USD,
    fundedVolumeUsd: FUNDED_VOLUME_USD,
    minMovePp: MIN_MOVE_PP,
  }
}
