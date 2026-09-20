/** Putting one exchange's fixture on another's.
 *
 *  🔑 This is the join everything else on the site now leans on, and it is the
 *     one this project has been bitten by most often. A wrong join does not
 *     blunt a price, it shows you Kalshi's Lecce quote in Milan's row. So:
 *
 *     - WHEN has to agree, and it is checked first, because it is a far
 *       stronger discriminator than any name scorer and it is free. By the ET
 *       calendar date both schedules file a game under where both feeds carry
 *       one, and by the kick-off instant otherwise.
 *     - Names are scored WHOLE with the alias-aware scorer, never matched as
 *       substrings. "Real Salt Lake" and "Real Monarchs" share a token.
 *     - The crossed orientation is tested too. If Kalshi's home also reads as
 *       Polymarket's away, the pairing is not a pairing.
 *     - The result must be ONE-TO-ONE. Two Kalshi events landing on one
 *       Polymarket fixture, or the reverse, drops BOTH rather than picking.
 *
 *  ⚠️ Kalshi publishes no kick-off for football. `occurrence_datetime` is the
 *     expected SETTLEMENT — measured at kick-off + 3h on 55 of 67 pairs, and
 *     +2h to +4.5h on the rest. The date comes off its event ticker instead.
 *
 *  Measured on a live board, 2026-09-20: 39 of Kalshi's soccer fixtures placed
 *  on Polymarket's 185, zero ambiguous.
 */

import { sameClub, teamScore } from './teamMatch'

/** The alias-aware scorer's bar for "these are the same club". Below it the
 *  pairing is unknown, never a guess. */
export const MIN_SIDE_SCORE = 0.6

/** Only used where one of the two feeds has no ET date to file the game
 *  under. Three hours absorbs a listed-time error without letting a different
 *  matchday in. */
export const MAX_KICKOFF_GAP_MS = 3 * 3600_000

export interface Sided {
  home: string
  away: string
  /** The instant the game starts, where the feed publishes one. Kalshi does
   *  not, for football. */
  kickoff?: string | null
  /** The Eastern calendar date the game is filed under.
   *
   *  🔑 Preferred over the instant whenever both sides carry it. Kalshi does
   *     NOT publish a kick-off for football: `occurrence_datetime` is the
   *     expected SETTLEMENT, which measured kick-off + 3h on 55 of 67 pairs
   *     and +2h, +3.5h or +4.5h on the rest. Treating it as a start time and
   *     allowing a ±3h window "worked" only because the true offset sat
   *     exactly on the boundary — and silently dropped every competition
   *     whose games run longer. The ET date comes off Kalshi's own event
   *     ticker and is exact. */
  etDate?: string | null
}

function timeOf(x: Sided): number | null {
  if (!x.kickoff) return null
  const t = Date.parse(x.kickoff)
  return Number.isFinite(t) ? t : null
}

/** Do these two rows describe the same fixture?
 *
 *  Fails closed at every step. A wrong join does not blunt a price, it shows
 *  one fixture's quote in another's row. */
export function sameFixture(a: Sided, b: Sided): boolean {
  // When ── the strongest and cheapest discriminator, and never skipped.
  if (a.etDate && b.etDate) {
    if (a.etDate !== b.etDate) return false
  } else {
    const ta = timeOf(a)
    const tb = timeOf(b)
    // Names alone have put Inter Miami on Inter before; nothing here runs on
    // names alone.
    if (ta == null || tb == null) return false
    if (Math.abs(ta - tb) > MAX_KICKOFF_GAP_MS) return false
  }

  // `sameClub` is the scorer PLUS the hand table of clubs Kalshi spells its
  // own way — "SL Benfica" for "Sport Lisboa e Benfica", which scores 0.25 and
  // cost this join the Porto v Benfica fixture on 2026-09-20.
  if (!sameClub(a.home, b.home) || !sameClub(a.away, b.away)) return false

  // The crossed reading still has to score worse. An alias makes a pairing
  // possible; it never makes one unique, and on a derby where both sides share
  // a city token that is exactly the case that would invert the board. An
  // aliased pair is exempt, because its scores are 0.25-ish by construction.
  const hh = teamScore(a.home, b.home)
  const aa = teamScore(a.away, b.away)
  const ha = teamScore(a.home, b.away)
  const ah = teamScore(a.away, b.home)
  if (Math.min(hh, aa) > Math.max(ha, ah)) return true
  return hh < MIN_SIDE_SCORE || aa < MIN_SIDE_SCORE
}

/** One-to-one placement of `others` onto `base`.
 *
 *  Returns a map from the base row's key to the other row. Anything ambiguous
 *  in either direction is left out and counted. */
export function placeVenue<B extends Sided, O extends Sided>(
  base: B[],
  keyOf: (b: B) => string,
  others: O[]
): { placed: Map<string, O>; dropped: number } {
  const hits = new Map<string, O[]>()
  const claims = new Map<O, string[]>()

  for (const o of others) {
    const matched = base.filter((b) => sameFixture(b, o))
    claims.set(o, matched.map(keyOf))
    for (const b of matched) {
      const k = keyOf(b)
      const list = hits.get(k)
      if (list) list.push(o)
      else hits.set(k, [o])
    }
  }

  const placed = new Map<string, O>()
  let dropped = 0
  for (const [k, list] of Array.from(hits.entries())) {
    // Two of theirs on one of ours: which is which cannot be known, so neither
    // is shown.
    if (list.length !== 1) {
      dropped += list.length
      continue
    }
    // One of theirs on two of ours: same problem from the other side.
    if ((claims.get(list[0]) ?? []).length !== 1) {
      dropped++
      continue
    }
    placed.set(k, list[0])
  }
  return { placed, dropped }
}
