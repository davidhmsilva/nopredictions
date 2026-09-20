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
  /** ⚠️ NOT a kick-off. `occurrence_datetime` is when Kalshi expects to
   *  settle: measured at kick-off + 3h on 55 of 67 fixtures and +2h to +4.5h
   *  on the rest. Kept because it bounds the day, never used as a start. */
  settlesAt: string | null
  /** The Eastern date off the event ticker — `KXBRASILEIROGAME-26SEP20VITCRU`
   *  → `20260920`. This is what the cross-venue join runs on. */
  etDate: string | null
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
