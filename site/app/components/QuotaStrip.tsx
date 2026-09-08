'use client'

/** How much of today is left, above the box you type into.
 *
 *  It exists so the limit is never a surprise. The alternative — finding out
 *  by writing a theory, waiting for the run, and getting a refusal — spends
 *  the user's attention to deliver bad news we already knew.
 *
 *  Renders nothing at all for a Pro account: an unlimited plan does not need
 *  a counter, and a strip that says "unlimited" on every page is chrome.
 */

import Link from 'next/link'
import type { Quota } from '../lib/useSession'

export function QuotaStrip({
  quota,
  signedIn,
  feature,
  next,
}: {
  quota: Quota | null
  signedIn: boolean
  feature: 'Lab test' | 'wallet read'
  /** Where to come back to after signing in. */
  next: string
}) {
  if (!signedIn) {
    return (
      <div className="np-quota is-out">
        <span>
          A free account gets <b className="np-num">3</b> {feature}s a day. No card.
        </span>
        <Link href={`/login?next=${encodeURIComponent(next)}`}>Sign in or create one →</Link>
      </div>
    )
  }

  // Pro, or the answer has not arrived — either way there is nothing useful to
  // say, and an empty strip is better than a wrong one.
  if (!quota || quota.limit === null) return null

  const out = quota.remaining === 0
  return (
    <div className={`np-quota${out ? ' is-out' : ''}`}>
      {out ? (
        <span>
          That is today&apos;s <b className="np-num">{quota.limit}</b>. The count resets at
          00:00 UTC.
        </span>
      ) : (
        <span>
          <b className="np-num">{quota.remaining}</b> of{' '}
          <b className="np-num">{quota.limit}</b> {feature}s left today.
        </span>
      )}
      <Link href="/pricing">{out ? 'Pro removes the limit →' : 'Go unlimited →'}</Link>
    </div>
  )
}
