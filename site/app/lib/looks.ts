// What a bettor watching the match is actually looking for: a short, ranked
// list of prices worth acting on, each with a number beside it that came from
// somewhere real.
//
// The rule the whole module obeys: never put a fair value next to a price
// unless we measured it. Where nothing was measured the price still gets shown
// — with no opinion attached — because "there is no read here" is a useful
// thing for a companion to say quickly, and a much better thing than a model
// output we already know loses to Polymarket on Brier score in 6 groups of 6.

import LATE_GOALS from './late_goals.json'
import FIRST_HALF from './first_half.json'
import {
  bucketOf,
  matchTotalLine,
  takerFeePp,
  type BookSide,
  type BoardState,
  type LiveState,
  type MarketGroup,
  type Outcome,
} from './gamecenter'

// ── the measured tables ──────────────────────────────────────────────────────

interface LateCell { n: number; p: number; p2: number }
interface LateTable {
  built_at: string
  minutes: number[]
  buckets: Array<{ name: string; lo: number; hi: number }>
  min_cell_n: number
  cells: Record<string, LateCell>
}
const LATE = LATE_GOALS as unknown as LateTable

interface HalfCell { n: number; p: number }
interface HalfTable {
  built_at: string
  minutes: number[]
  min_cell_n: number
  cells: Record<string, HalfCell>
}
const HALF = FIRST_HALF as unknown as HalfTable

// Understat's minute-level goal data covers the Big 5 plus the Russian top
// flight. Anywhere else the rate is an import, not a measurement of that
// league — the look carries a flag and the page shows it.
const MEASURED_UNIVERSE =
  /premier league|la ?liga|serie a|bundesliga|ligue 1|rfpl|russian premier/i

/** Measured P(at least `needed` more goals) given (minute, goals, pre-match bucket).
 *
 *  Snaps to the table's 2-minute grid and falls back to the pooled cell when the
 *  bucketed one is thin. Never derives the two-goal number from the one-goal
 *  number: football is underdispersed against Poisson late on, so P(>=2) runs
 *  0.63x the Poisson value by 86'. The table carries a measured p2. */
export function lateRate(
  minute: number,
  goals: number,
  pOver25: number | null,
  needed: 1 | 2 = 1
): { p: number; n: number; bucket: string } | null {
  const tm = LATE.minutes.reduce((a, b) =>
    Math.abs(b - minute) < Math.abs(a - minute) ? b : a
  )
  if (Math.abs(tm - minute) > 3) return null
  const field = needed === 1 ? 'p' : 'p2'
  for (const bucket of [bucketOf(pOver25), 'all']) {
    const cell = LATE.cells[`${tm}|${goals}|${bucket}`]
    if (cell && cell.n >= LATE.min_cell_n) return { p: cell[field], n: cell.n, bucket }
  }
  return null
}

/** Measured P(a goal before half time | still 0-0 at `minute`).
 *
 *  The STATE comes from minute-level goal data but the OUTCOME never does:
 *  Understat folds first-half stoppage into the neighbouring minutes, and a
 *  45+2 goal is exactly what this market pays on. The table's outcome is the
 *  recorded half-time score. */
export function firstHalfRate(
  minute: number,
  pOver25: number | null
): { p: number; n: number; bucket: string } | null {
  const tm = HALF.minutes.reduce((a, b) =>
    Math.abs(b - minute) < Math.abs(a - minute) ? b : a
  )
  if (Math.abs(tm - minute) > 2) return null
  for (const bucket of [bucketOf(pOver25), 'all']) {
    const cell = HALF.cells[`${tm}|${bucket}`]
    if (cell && cell.n >= HALF.min_cell_n) return { p: cell.p, n: cell.n, bucket }
  }
  return null
}

// ── the look ─────────────────────────────────────────────────────────────────

export type LookTier = 'measured' | 'board' | 'price'
export type LookCall = 'back' | 'fair' | 'rich'

export interface Look {
  id: string
  /** What you would back, in the words a bettor uses: "Over 2.5". */
  side: string
  /** The market it sits in, for anyone who wants to check they read it right. */
  market: string
  tier: LookTier
  call: LookCall
  /** Decimal at the ask — the price you would actually pay. */
  odds: number
  prob: number
  fairOdds: number | null
  fairProb: number | null
  /** Matches behind the fair number. */
  n: number | null
  /** fairProb - prob, in points, AFTER the taker fee. Positive = cheap. */
  edgePp: number | null
  feePp: number
  spreadPp: number | null
  depthUsd: number | null
  /** True when there is no CLOB book and the price is a Gamma mid. */
  isMid: boolean
  /** True when the measured rate comes from leagues other than this one. */
  imported: boolean
  /** One line. Not a paragraph. */
  why: string
  /** Everything that would otherwise clutter the line, behind a click. */
  detail: string[]
  tokenId: string | null
}

// A price below this is not worth a bettor's attention after the fee, and a
// look that cannot be traded is not a look at all.
const MIN_EDGE_PP = 2.0
const MIN_DEPTH_USD = 25
// Outside this band the decimal price stops describing a bet anyone would place.
const TRADEABLE_BAND: [number, number] = [0.03, 0.97]

function dec(p: number): number {
  return 1 / p
}

function priceOf(o: Outcome | undefined): { ask: number; book: BookSide | null; isMid: boolean } | null {
  if (!o) return null
  if (o.book?.ask != null) return { ask: o.book.ask, book: o.book, isMid: false }
  return o.price != null ? { ask: o.price, book: null, isMid: true } : null
}

function sideOf(g: MarketGroup, re: RegExp): Outcome | undefined {
  return g.outcomes.find((o) => re.test(o.name))
}

/** One side of one market, priced against a measured rate. */
function makeLook(
  args: {
    id: string
    side: string
    group: MarketGroup
    outcome: Outcome | undefined
    fairProb: number
    n: number
    tier: LookTier
    imported: boolean
    why: (edgePp: number, fair: number, ask: number) => string
    detail: string[]
  }
): Look | null {
  const p = priceOf(args.outcome)
  if (!p) return null
  if (p.ask <= TRADEABLE_BAND[0] || p.ask >= TRADEABLE_BAND[1]) return null

  const feePp = takerFeePp(p.ask)
  const edgePp = (args.fairProb - p.ask) * 100 - feePp
  const spreadPp =
    p.book?.bid != null && p.book?.ask != null ? (p.book.ask - p.book.bid) * 100 : null
  const depthUsd = p.book?.askDepthUsd ?? null

  const call: LookCall =
    edgePp >= MIN_EDGE_PP ? 'back' : edgePp <= -MIN_EDGE_PP ? 'rich' : 'fair'

  return {
    id: args.id,
    side: args.side,
    market: args.group.question,
    tier: args.tier,
    call,
    odds: dec(p.ask),
    prob: p.ask,
    fairOdds: dec(args.fairProb),
    fairProb: args.fairProb,
    n: args.n,
    edgePp,
    feePp,
    spreadPp,
    depthUsd,
    isMid: p.isMid,
    imported: args.imported,
    why: args.why(edgePp, args.fairProb, p.ask),
    detail: args.detail,
    tokenId: args.outcome?.tokenId ?? null,
  }
}

/** Is this look something a bettor could actually get on? */
export function tradeable(l: Look): boolean {
  return !l.isMid && (l.depthUsd == null || l.depthUsd >= MIN_DEPTH_USD)
}

/** The ranked shortlist.
 *
 *  Both sides of every market we can measure, because a rich over IS a cheap
 *  under and the under is the side you can actually back. Sorted by edge after
 *  the fee, backs first, and only among prices with a real book — a Gamma mid
 *  is not a price, and ranking one first is how a paper strategy books +141%
 *  where the same 15 decisions returned +3.4% live.
 */
export function buildLooks(
  groups: MarketGroup[],
  live: LiveState | null,
  board: BoardState,
  preOver25: number | null,
  competition: string | null
): Look[] {
  const looks: Look[] = []

  // The clock is a PUBLISHED minute — api-football's `elapsed`, or Polymarket's
  // own live block, which is the minute it shows the trader on the same event.
  // What is never used is Polymarket's listed START TIME: that ran ~30 min
  // early on some leagues and eight hours late on others, and inferring a
  // minute from it is what made 73k of our own observations unusable.
  //
  // ⚠️ Polymarket's score is read as a TOTAL and as "still 0-0", never as
  //    home-vs-away: its title order cannot be trusted for sides, and every
  //    table below is keyed on the total anyway.
  const clocked = live?.clockSource === 'api-football' || live?.clockSource === 'polymarket'
  const minute = clocked ? live!.minute : null
  const goals = clocked
    ? live!.homeGoals + live!.awayGoals
    : board.certain
      ? board.goals
      : null

  const imported = !MEASURED_UNIVERSE.test(competition ?? '')
  const universeNote = imported
    ? `Rate measured on Big-5 European club football plus the Russian top flight ` +
      `(Understat, minute-level goal data). ${competition ?? 'This competition'} is outside ` +
      `that sample, so it is an imported rate rather than a measurement of this league.`
    : `Rate measured on Big-5 European club football with minute-level goal data (Understat).`

  // ── a. the goal ladder, one and two rungs above the current score.
  if (minute != null && goals != null) {
    for (const needed of [1, 2] as const) {
      const rate = lateRate(minute, goals, preOver25, needed)
      if (!rate) continue
      const line = goals + needed - 0.5
      const g = groups.find((x) => matchTotalLine(x.question) === line)
      if (!g) continue

      const label = needed === 1 ? 'one more goal' : 'two more goals'
      const detail = [
        universeNote,
        `Conditioned on the exact state: ${goals} goal${goals === 1 ? '' : 's'} scored, ` +
          `${minute}' on the clock, and this fixture's pre-match Over 2.5 in the ` +
          `"${rate.bucket}" bucket. ${rate.n.toLocaleString()} matches sit in that cell.`,
        needed === 2
          ? `The two-goal rate is measured directly, never derived from the one-goal rate. ` +
            `Football is underdispersed against Poisson late on — empirical P(>=2 more) runs ` +
            `0.63x the Poisson value by 86' — so extrapolating would overprice this rung.`
          : `The pre-match total still predicts a late goal once the state is fixed: at 75' ` +
            `with one goal scored, the lowest bucket paid 2.23 and the highest 1.89, on n=4,444 ` +
            `with disjoint confidence intervals. That is why the bucket is in the lookup.`,
      ]

      const over = makeLook({
        id: `over-${line}`,
        side: `Over ${line}`,
        group: g,
        outcome: sideOf(g, /^over$/i),
        fairProb: rate.p,
        n: rate.n,
        tier: 'measured',
        imported,
        why: (edge, fair, ask) =>
          edge >= MIN_EDGE_PP
            ? `${rate.n.toLocaleString()} matches in this exact state delivered ${label} ` +
              `${(fair * 100).toFixed(0)}% of the time. Polymarket wants ${dec(ask).toFixed(2)}.`
            : edge <= -MIN_EDGE_PP
              ? `Polymarket asks ${dec(ask).toFixed(2)} where the measured rate paid ` +
                `${dec(fair).toFixed(2)}. The over is the expensive side here.`
              : `Polymarket's ${dec(ask).toFixed(2)} sits inside the fee of the measured ` +
                `${dec(fair).toFixed(2)}. Nothing to do.`,
        detail,
      })

      const under = makeLook({
        id: `under-${line}`,
        side: `Under ${line}`,
        group: g,
        outcome: sideOf(g, /^under$/i),
        fairProb: 1 - rate.p,
        n: rate.n,
        tier: 'measured',
        imported,
        why: (edge, fair, ask) =>
          edge >= MIN_EDGE_PP
            ? `${label.charAt(0).toUpperCase() + label.slice(1)} failed to arrive in ` +
              `${(fair * 100).toFixed(0)}% of ${rate.n.toLocaleString()} matches in this state. ` +
              `Polymarket wants ${dec(ask).toFixed(2)}.`
            : edge <= -MIN_EDGE_PP
              ? `Polymarket asks ${dec(ask).toFixed(2)} where the measured rate paid ` +
                `${dec(fair).toFixed(2)}. The under is the expensive side here.`
              : `Polymarket's ${dec(ask).toFixed(2)} sits inside the fee of the measured ` +
                `${dec(fair).toFixed(2)}. Nothing to do.`,
        detail,
      })

      if (over) looks.push(over)
      if (under) looks.push(under)
    }
  }

  // ── b. first half over 0.5, while it is still 0-0 and the half is running.
  if (minute != null && minute >= 10 && minute <= 44 && goals === 0) {
    const rate = firstHalfRate(minute, preOver25)
    const g = groups.find((x) => /1st half.*O\/U\s*0\.5/i.test(x.question))
    if (rate && g) {
      const detail = [
        universeNote,
        `State from minute-level goal data, outcome from the recorded half-time score — ` +
          `never from the goal feed. Understat folds first-half stoppage into the neighbouring ` +
          `minutes and disagrees with the half-time score on 1.4% of matches, and a 45+2 goal ` +
          `is exactly what this market pays on.`,
        `${rate.n.toLocaleString()} matches were still 0-0 at ${minute}' with this fixture's ` +
          `pre-match total. The pre-match total survives into the 0-0 state: at 15' the low ` +
          `bucket paid 2.02 and the high bucket 1.55.`,
        `Polymarket has historically been RICH on this exact market — its price sat above the ` +
          `realised frequency at every minute tested from 5' to 40' (n=236 fixtures ` +
          `reconstructed from the order book, about 4pp, confidence interval crossing zero).`,
      ]
      const over = makeLook({
        id: 'fh-over-05',
        side: 'Over 0.5 — first half',
        group: g,
        outcome: sideOf(g, /^over$/i),
        fairProb: rate.p,
        n: rate.n,
        tier: 'measured',
        imported,
        why: (edge, fair, ask) =>
          edge >= MIN_EDGE_PP
            ? `Still 0-0 at ${minute}'. ${rate.n.toLocaleString()} matches in that spot saw a ` +
              `goal before the break ${(fair * 100).toFixed(0)}% of the time; Polymarket wants ` +
              `${dec(ask).toFixed(2)}.`
            : `Polymarket asks ${dec(ask).toFixed(2)} against a measured ${dec(fair).toFixed(2)} ` +
              `— and it is usually rich on this market.`,
        detail,
      })
      const under = makeLook({
        id: 'fh-under-05',
        side: 'Under 0.5 — first half',
        group: g,
        outcome: sideOf(g, /^under$/i),
        fairProb: 1 - rate.p,
        n: rate.n,
        tier: 'measured',
        imported,
        why: (edge, fair, ask) =>
          edge >= MIN_EDGE_PP
            ? `${rate.n.toLocaleString()} matches still 0-0 at ${minute}' reached the break ` +
              `goalless ${(fair * 100).toFixed(0)}% of the time. Polymarket wants ` +
              `${dec(ask).toFixed(2)}.`
            : `Polymarket asks ${dec(ask).toFixed(2)} against a measured ${dec(fair).toFixed(2)}.`,
        detail,
      })
      if (over) looks.push(over)
      if (under) looks.push(under)
    }
  }

  // Backs first, then by edge. A price with no book can still be informative but
  // never ranks above one you could pay.
  const rank = (l: Look) =>
    (tradeable(l) ? 0 : 1000) + (l.call === 'back' ? 0 : 100) - (l.edgePp ?? 0)
  return looks.sort((a, b) => rank(a) - rank(b))
}

// ── what changed while you were watching ─────────────────────────────────────

export interface Pulse {
  question: string
  outcome: string
  from: number
  to: number
  movePp: number
  minutesAgo: number
}

/** Price moves inside the last `windowMin` minutes, biggest first.
 *
 *  This is the axis a companion needs. Twenty-four hours of history describes a
 *  fixture; the last quarter of an hour describes the game you are watching. */
export function buildPulse(
  histories: Array<{ question: string; outcome: string; points: Array<{ t: number; p: number }> }>,
  windowMin = 20,
  minMovePp = 2
): Pulse[] {
  const cutoff = Date.now() / 1000 - windowMin * 60
  const out: Pulse[] = []

  for (const h of histories) {
    const recent = h.points.filter((p) => p.t >= cutoff)
    if (recent.length < 2) continue
    const from = recent[0]
    const to = recent[recent.length - 1]
    const movePp = (to.p - from.p) * 100
    if (Math.abs(movePp) < minMovePp) continue
    out.push({
      question: h.question,
      outcome: h.outcome,
      from: from.p,
      to: to.p,
      movePp,
      minutesAgo: Math.max(0, Math.round((Date.now() / 1000 - from.t) / 60)),
    })
  }

  return out.sort((a, b) => Math.abs(b.movePp) - Math.abs(a.movePp))
}
