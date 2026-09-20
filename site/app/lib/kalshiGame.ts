/** One fixture's Kalshi prices, laid against Polymarket's own markets.
 *
 *  The board answers "which exchange is cheaper for the 1X2 and Over 2.5".
 *  This answers it market by market, on the Game Center's Markets tab, which
 *  is where someone has already decided WHAT to bet and only wants to know
 *  where to buy it.
 *
 *  ⚠️ Matching is by the MARKET, never by the text of a question. Polymarket
 *     splits the 1X2 into three binary markets whose outcomes are just
 *     "Yes"/"No" — the team lives in the question — and its totals family
 *     includes team totals, corner totals and half totals that read exactly
 *     like the match-goals ladder. The comparison this replaces did
 *     `question.includes(kalshiName.split(' ')[0])`, which is the substring
 *     match this project has been bitten by repeatedly. Everything here fails
 *     closed: an outcome we cannot place gets no Kalshi price rather than the
 *     wrong one.
 */

import type { KalshiFixture, KalshiLeg } from './kalshiSoccerTypes'
import type { MarketGroup } from './gamecenter'
import { teamScore, MIN_SIDE_SCORE } from './teamMatch'
import { bestOf, gradeOf, type BestPick, type BookGrade, type Quote } from './venues'

export interface KalshiQuoteRef {
  ticker: string
  /** Kalshi's own wording for the leg. Shown in the tooltip, so a reader can
   *  check that the two markets really are the same bet. */
  label: string
  quote: Quote
  /** Which leg of Kalshi's binary we are buying. An Under is the NO side of
   *  the same Over ticker, which is a real price rather than a derived one: on
   *  a binary book, buying NO at 1 − yes_bid IS selling YES at the bid. */
  side: 'yes' | 'no'
}

export interface KalshiGame {
  eventTicker: string
  competition: string
  url: string
  /** Read off Kalshi's 1X2, the ladder both venues always quote. */
  grade: BookGrade
  volume: number | null
  /** `outcomeKey(question, outcome)` → Kalshi's price for the SAME bet. */
  byOutcome: Record<string, KalshiQuoteRef>
  /** How many Polymarket outcomes we could put a Kalshi price against. */
  matched: number
}

/** The key both sides of the comparison agree on. */
export function outcomeKey(question: string, outcome: string): string {
  return `${question}|${outcome}`
}

/** The NO leg of a binary market. */
function noSide(q: Quote): Quote {
  const bid = q.ask == null ? null : 1 - q.ask
  const ask = q.bid == null ? null : 1 - q.bid
  return {
    bid,
    ask,
    spread: q.spread,
    // The depth behind a NO ask is the size resting on the YES bid, which the
    // event feed does not carry. Null is "not known", never "nothing there".
    askDepthUsd: null,
  }
}

/** The match-goals ladder, and nothing that merely reads like it.
 *
 *  "A vs. B: O/U 2.5" is the match total. "A vs. B: Everton FC O/U 2.5" is a
 *  team total and "… O/U 2.5 Corners" is corners — both of which quote
 *  entirely different numbers, and grading Arsenal v Chelsea off a corners
 *  book on an unopened line is how this site once called a $1.18M market
 *  blown. */
const MATCH_TOTAL = /:\s*O\/U\s+(\d+\.5)\s*$/i

const DRAW_RE = /\bend(s|ing)? in a (draw|tie)\b|\bdraw\b|\btie\b/i
const WIN_RE = /^Will\s+(.+?)\s+win\b/i

type Side = 'home' | 'draw' | 'away'

/** Which side of the fixture a Polymarket moneyline question is about.
 *
 *  Side errors here invert the comparison rather than blunt it, so the name is
 *  scored against both teams with the alias-aware scorer and anything that
 *  reads equally well as either is refused. */
function moneylineSide(question: string, home: string, away: string): Side | null {
  if (DRAW_RE.test(question)) return 'draw'
  const m = WIN_RE.exec(question)
  if (!m) return null
  const team = m[1].trim()
  const sh = teamScore(team, home)
  const sa = teamScore(team, away)
  if (Math.max(sh, sa) < MIN_SIDE_SCORE) return null
  if (Math.abs(sh - sa) < 0.05) return null
  return sh > sa ? 'home' : 'away'
}

/** Polymarket's 1X2 markets are binary: the "Yes" outcome is the bet, and its
 *  "No" is the other two results together — which Kalshi does not list as one
 *  market. So only the Yes leg gets a comparison. */
const YES_RE = /^yes$/i
const OVER_RE = /^over$/i
const UNDER_RE = /^under$/i

export function matchKalshiMarkets(
  groups: MarketGroup[],
  home: string,
  away: string,
  k: KalshiFixture
): KalshiGame {
  const byOutcome: Record<string, KalshiQuoteRef> = {}
  const put = (question: string, outcome: string, leg: KalshiLeg, side: 'yes' | 'no') => {
    byOutcome[outcomeKey(question, outcome)] = {
      ticker: leg.ticker,
      label: leg.label,
      quote: side === 'yes' ? leg.quote : noSide(leg.quote),
      side,
    }
  }

  for (const g of groups) {
    // ── the 1X2 ──
    if (g.group === 'Match result') {
      const side = moneylineSide(g.question, home, away)
      const leg = side ? k.legs[side] : null
      const yes = g.outcomes.find((o) => YES_RE.test(o.name))
      if (leg && yes) put(g.question, yes.name, leg, 'yes')
      continue
    }

    // ── the match-goals ladder ──
    const m = MATCH_TOTAL.exec(g.question)
    if (!m) continue
    const leg = k.totals[parseFloat(m[1]).toFixed(1)]
    if (!leg) continue
    for (const o of g.outcomes) {
      if (OVER_RE.test(o.name)) put(g.question, o.name, leg, 'yes')
      else if (UNDER_RE.test(o.name)) put(g.question, o.name, leg, 'no')
    }
  }

  return {
    eventTicker: k.eventTicker,
    competition: k.competition,
    url: k.url,
    grade: gradeOf([k.legs.home?.quote, k.legs.draw?.quote, k.legs.away?.quote]),
    volume: k.volume,
    byOutcome,
    matched: Object.keys(byOutcome).length,
  }
}

/** Where to buy one outcome, net of each venue's taker fee. Null when Kalshi
 *  does not quote it — there is nothing to compare, which is not the same as
 *  the two being level.
 *
 *  Each side is graded on its OWN book here, not on the fixture's, because
 *  this is a per-market question: a fixture with a clean 1X2 can still have a
 *  10¢-wide Over 4.5, and buying at the cheap end of that is not a saving. */
export function pickFor(pm: Quote, ref: KalshiQuoteRef | undefined): BestPick | null {
  if (!ref) return null
  return bestOf([
    { venue: 'polymarket', quote: pm, grade: gradeOf([pm]) },
    { venue: 'kalshi', quote: ref.quote, grade: gradeOf([ref.quote]) },
  ])
}
