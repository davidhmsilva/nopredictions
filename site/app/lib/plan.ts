/** Plans, quotas, and the one place that decides whether an action is allowed.
 *
 *  The shape the product settled on:
 *
 *    no account   Scout · Agent · Game Center            unlimited
 *    free         + Lab and Wallet                       3 a day, each
 *    pro          + Lab and Wallet unlimited, alerts,
 *                   a synced watchlist                   $19/mo · $190/yr
 *
 *  🔑 Every read and write here goes over DATABASE_URL, never over the anon
 *     key. `usage_events` has RLS on with NO policies (db/044), so the browser
 *     cannot read a count, forge one, or delete one to start the day again.
 *     A quota the client can reach is not a quota.
 */

import { getSql } from './db'

export type Plan = 'free' | 'pro'
export type MeteredFeature = 'lab' | 'wallet'

/** What a free account gets per UTC day, per feature. */
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

export interface Entitlement {
  plan: Plan
  /** Null when the plan is unlimited. */
  limit: number | null
  used: number
  remaining: number | null
  allowed: boolean
  /** Why a request was refused, for a message the user can act on. */
  reason: 'ok' | 'signed_out' | 'quota_exhausted'
}

export const SIGNED_OUT: Entitlement = {
  plan: 'free',
  limit: 0,
  used: 0,
  remaining: 0,
  allowed: false,
  reason: 'signed_out',
}

interface ProfileRow {
  plan: Plan
  plan_status: string | null
  current_period_end: Date | null
  /** 'owner' = the site's operator (db/049). Set by hand in SQL, never by a client. */
  role?: string | null
}

/** Pro until the period they paid for actually ends.
 *
 *  Someone who cancels on day 3 of a month has bought that month. Stripe keeps
 *  the subscription `active` until the period end and only then sends the
 *  event that flips the row, but a webhook can be missed — so the date is
 *  checked here too rather than trusted to have arrived. */
function isPro(p: ProfileRow | undefined): boolean {
  // The operator's own account is unmetered: it is the one testing everything.
  if (p?.role === 'owner') return true
  if (!p || p.plan !== 'pro') return false
  if (!p.current_period_end) return true
  return p.current_period_end.getTime() > Date.now()
}

export async function planOf(userId: string): Promise<Plan> {
  const sql = getSql()
  const rows = await sql<ProfileRow[]>`
    select plan, plan_status, current_period_end, role
      from public.profiles
     where id = ${userId}
  `
  return isPro(rows[0]) ? 'pro' : 'free'
}

/** Plan and role together — the agents page needs both. */
export async function accountOf(userId: string): Promise<{ plan: Plan; role: 'user' | 'owner' }> {
  const sql = getSql()
  const rows = await sql<ProfileRow[]>`
    select plan, plan_status, current_period_end, role
      from public.profiles
     where id = ${userId}
  `
  return {
    plan: isPro(rows[0]) ? 'pro' : 'free',
    role: rows[0]?.role === 'owner' ? 'owner' : 'user',
  }
}

/** What this user may do right now, without spending anything. */
export async function entitlement(
  userId: string | null,
  feature: MeteredFeature
): Promise<Entitlement> {
  if (!userId) return SIGNED_OUT

  const sql = getSql()
  const [profile] = await sql<ProfileRow[]>`
    select plan, plan_status, current_period_end, role
      from public.profiles
     where id = ${userId}
  `

  if (isPro(profile)) {
    return { plan: 'pro', limit: null, used: 0, remaining: null, allowed: true, reason: 'ok' }
  }

  const limit = FREE_DAILY_LIMIT[feature]
  const [{ n }] = await sql<{ n: number }[]>`
    select count(*)::int as n
      from public.usage_events
     where user_id = ${userId}
       and feature = ${feature}
       and created_at >= date_trunc('day', now() at time zone 'utc')
  `
  return {
    plan: 'free',
    limit,
    used: n,
    remaining: Math.max(0, limit - n),
    allowed: n < limit,
    reason: n < limit ? 'ok' : 'quota_exhausted',
  }
}

/** Claim one use, atomically, and say whether it was granted.
 *
 *  ⚠️ Checking then inserting is a race: two requests that arrive together
 *     both read `used = 2`, both decide 2 < 3, and both run. At three a day
 *     that is not a rounding error, it is a third of the tier. So the count
 *     and the insert happen inside one transaction behind an advisory lock
 *     keyed on the user — the lock is released when the transaction ends, and
 *     it serialises only that user's own requests.
 */
export async function claimUse(
  userId: string | null,
  feature: MeteredFeature
): Promise<Entitlement> {
  if (!userId) return SIGNED_OUT

  const sql = getSql()
  return sql.begin(async (tx) => {
    const [profile] = await tx<ProfileRow[]>`
      select plan, plan_status, current_period_end, role
        from public.profiles
       where id = ${userId}
    `
    if (isPro(profile)) {
      // Pro is unmetered, so it is not recorded. Nothing downstream reads
      // usage for a pro account, and writing rows nobody reads is how a table
      // becomes a million rows of confusion.
      return { plan: 'pro', limit: null, used: 0, remaining: null, allowed: true, reason: 'ok' } as Entitlement
    }

    // One waiter per user, not a global gate.
    await tx`select pg_advisory_xact_lock(hashtextextended(${userId}, 0))`

    const limit = FREE_DAILY_LIMIT[feature]
    const [{ n }] = await tx<{ n: number }[]>`
      select count(*)::int as n
        from public.usage_events
       where user_id = ${userId}
         and feature = ${feature}
         and created_at >= date_trunc('day', now() at time zone 'utc')
    `
    if (n >= limit) {
      return {
        plan: 'free', limit, used: n, remaining: 0,
        allowed: false, reason: 'quota_exhausted',
      } as Entitlement
    }

    await tx`
      insert into public.usage_events (user_id, feature)
      values (${userId}, ${feature})
    `
    return {
      plan: 'free', limit, used: n + 1, remaining: limit - n - 1,
      allowed: true, reason: 'ok',
    } as Entitlement
  })
}

/** The message a refused request should carry. One wording, one place. */
export function refusalMessage(e: Entitlement): string {
  if (e.reason === 'signed_out') {
    return 'Sign in to run this — free accounts get 3 a day.'
  }
  if (e.reason === 'quota_exhausted') {
    return `That is today's ${e.limit} on the free plan. Pro removes the limit, or the count resets at 00:00 UTC.`
  }
  return 'Not allowed.'
}

/** Give a use back.
 *
 *  Called when the request failed for OUR reason — a parse that errored, a
 *  provider that was down. Charging someone a third of their daily allowance
 *  for our 502 is the kind of small unfairness that loses an account, and the
 *  fix is one delete of the most recent row. */
export async function refundUse(
  userId: string | null,
  feature: MeteredFeature
): Promise<void> {
  if (!userId) return
  const sql = getSql()
  await sql`
    delete from public.usage_events
     where id = (
       select id from public.usage_events
        where user_id = ${userId} and feature = ${feature}
        order by created_at desc
        limit 1
     )
  `
}
