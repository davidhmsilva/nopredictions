/** The terms of each plan that a page also has to PRINT.
 *
 *  Split out of plan.ts because plan.ts talks to Postgres, and a client page
 *  that imported it for one constant would drag the driver into its bundle.
 *  The pricing page used to keep its own copy of the price for that reason —
 *  two numbers that nothing kept equal. Now there is one of each.
 */

export type MeteredFeature = 'lab' | 'wallet'

/** What a free account gets per day, per feature. */
export const FREE_DAILY_LIMIT: Record<MeteredFeature, number> = {
  lab: 3,
  wallet: 3,
}

/** Price, in one place, so the pricing page and Stripe cannot drift apart. */
export const PRICING = {
  monthlyUsd: 19,
  yearlyUsd: 190,
  /** Two months free, stated rather than left for the reader to work out. */
  yearlySavingUsd: 19 * 12 - 190,
} as const

/** Whose midnight ends a free day.
 *
 *  US Eastern since 2026-09-13; it was UTC. The customer this site is built
 *  for is in the US, and "resets at 00:00 UTC" is 8 PM in New York — the
 *  evening of the day it claims to be ending.
 *
 *  It needed no migration: `usage_events` stores instants, and a day is a
 *  window over them, so only where the window opens moved. The one side
 *  effect is on the switch day, which can hand back up to four hours of uses
 *  someone had already spent — and on 2026-09-13 the table had no rows. */
export const QUOTA_TZ = 'America/New_York'
export const QUOTA_RESET_TEXT = 'midnight ET'
