/** Who is asking, what they may do, and how much of it is left.
 *
 *  One request rather than three: the header needs the email, the Lab needs
 *  its remaining count and the Wallet needs its own, and they all render on
 *  the same page load.
 *
 *  Signed out is a 200 with `user: null`, not a 401. Being signed out is the
 *  normal state of this site — Scout, Agent and the Game Center never ask —
 *  and a header that logs an error on every anonymous visit is noise that
 *  hides the errors that matter.
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
      wallet: null,
    })
  }

  let plan: 'free' | 'pro' = 'free'
  let lab: Entitlement | null = null
  let wallet: Entitlement | null = null
  try {
    ;[plan, lab, wallet] = await Promise.all([
      planOf(user.id),
      entitlement(user.id, 'lab'),
      entitlement(user.id, 'wallet'),
    ])
  } catch (err) {
    // The database being unreachable must not sign anyone out. The header
    // still shows who they are; the counters just have nothing to say.
    console.error('/api/me entitlement lookup failed', err)
  }

  return NextResponse.json({
    user: { id: user.id, email: user.email ?? null },
    plan,
    limits: FREE_DAILY_LIMIT,
    lab,
    wallet,
  })
}
