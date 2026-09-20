/** Kalshi's soccer shapes, on their own so the browser can hold them.
 *
 *  `kalshiSoccer.ts` does the sweeping and re-exports these; nothing that only
 *  needs to READ a Kalshi fixture has to import the sweeper. */

import type { Quote } from './venues'

export type KalshiSide = 'home' | 'draw' | 'away'

export interface KalshiLeg {
  ticker: string
  /** Kalshi's own label for the leg — the team, or "Tie". */
  label: string
  quote: Quote
}

export interface KalshiFixture {
  eventTicker: string
  series: string
  /** The competition, from the series title: "Serie A", "Liga MX". */
  competition: string
  /** As Kalshi writes them. "Home vs Away" — verified against ESPN, Kalshi's
   *  own settlement source, on 3/3 fixtures 2026-07-22. Re-verify if the title
   *  shape ever changes; this is the bug class that bit the NBA scanner. */
  home: string
  away: string
  /** `occurrence_datetime` off the markets: an exact kick-off, which is what
   *  makes the join to Polymarket safe without a name-only guess. */
  kickoff: string | null
  url: string
  legs: Record<KalshiSide, KalshiLeg | null>
  /** The match-goals ladder, keyed by line ("2.5"), each the OVER leg — the
   *  same convention Polymarket's O/U markets use, so the two are directly
   *  comparable. Empty when Kalshi lists no goals market for the fixture. */
  totals: Record<string, KalshiLeg>
  /** Contracts traded across the three legs. Kalshi's contracts are $1, so
   *  this is comparable-ish with Polymarket dollars — close enough to RANK on
   *  and not close enough to quote as a total without saying so. */
  volume: number | null
}

export interface KalshiSoccerIndex {
  fixtures: KalshiFixture[]
  generatedAt: string
  /** How the sweep went. A board missing half of Kalshi because the exchange
   *  refused half our requests should say so, not read as "Kalshi lists 60
   *  games today". */
  series: number
  seriesOk: number
}
