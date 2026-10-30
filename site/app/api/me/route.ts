/** Who is asking, what they may do, and how much of it is left.
 *
 *  One request for the header and for Create's counter, which render on the
 *  same page load.
 *
 *  Signed out is a 200 with `user: null`, not a 401. Being signed out is a
 *  normal way to browse this site — the home page and the Marketplace never
 *  ask — and a header that logs an error on every anonymous visit is noise
 *  that hides the errors that matter.
 */

import { NextResponse } from 'next/server'
import { currentUser } from '../../lib/supabaseAuth'
import { entitlement, planOf, FREE_DAILY_LIMIT, type Entitlement } from '../../lib/plan'

export const dynamic = 'force-dynamic'

export async function GET() {
  const user = await currentUser()
  if (!user) {
    return NextResponse.json({
      user: null,
      plan: 'free',
      limits: FREE_DAILY_LIMIT,
      lab: null,
    })
  }

  let plan: 'free' | 'pro' = 'free'
  let lab: Entitlement | null = null
  try {
    ;[plan, lab] = await Promise.all([planOf(user.id), entitlement(user.id, 'lab')])
  } catch (err) {
    // The database being unreachable must not sign anyone out. The header
    // still shows who they are; the counter just has nothing to say.
    console.error('/api/me entitlement lookup failed', err)
  }

  return NextResponse.json({
    user: { id: user.id, email: user.email ?? null },
    plan,
    limits: FREE_DAILY_LIMIT,
    lab,
  })
}
