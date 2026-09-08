/** Newsletter signup.
 *
 *  🔑 The address is stored for real, and the form says out loud that nothing
 *     is being sent yet. That combination is deliberate: this site deleted a
 *     newsletter form on 2026-09-06 that set local state, showed a tick, and
 *     sent nowhere. Storing without sending is honest. Showing a tick without
 *     storing is not.
 *
 *  `newsletter_subscribers` has RLS on with no policies (db/046), so the anon
 *  key cannot reach it and this route is the only way in. Validation therefore
 *  lives here, where it can be tested, with the table's own constraints as the
 *  backstop if a future caller forgets.
 */

import { NextResponse } from 'next/server'
import { createHash } from 'crypto'
import { getSql } from '../../lib/db'

export const dynamic = 'force-dynamic'

/** Deliberately not RFC-complete — RFC 5322 allows addresses no provider
 *  issues. This is the same shape the table's CHECK constraint enforces, so
 *  the two cannot disagree about what an email is. */
const EMAIL =
  /^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$/

/** Three an hour from one address is generous for a household and useless to a
 *  bot, which wants thousands. Same number db/027 chose for the waitlist. */
const MAX_PER_HOUR = 3

function clientIp(request: Request): string {
  const fwd = request.headers.get('x-forwarded-for') ?? ''
  return fwd.split(',')[0]?.trim() ?? ''
}

export async function POST(request: Request) {
  let email = ''
  let source = 'insights'
  try {
    const body = await request.json()
    email = String(body?.email ?? '').trim().toLowerCase()
    // Where they signed up from — the first thing anyone asks of a list is
    // which page built it. Bounded so it cannot be used as free storage.
    source = String(body?.source ?? 'insights').trim().slice(0, 60) || 'insights'
  } catch {
    return NextResponse.json({ error: 'Invalid request.' }, { status: 400 })
  }

  if (email.length < 6 || email.length > 254 || !EMAIL.test(email) || email.includes('..')) {
    return NextResponse.json({ error: "That does not look like an email address." }, { status: 400 })
  }

  const sql = getSql()
  const domain = email.split('@')[1]

  try {
    // The disposable list is db/027's, reused rather than duplicated — one
    // list, one place to add to.
    const blocked = await sql<{ domain: string }[]>`
      select domain from public.waitlist_blocked_domains where domain = ${domain}
    `
    if (blocked.length) {
      return NextResponse.json(
        { error: 'Please use an address you actually read.' },
        { status: 400 }
      )
    }

    const ip = clientIp(request)
    const ipHash = ip ? createHash('md5').update(ip).digest('hex') : null

    if (ipHash) {
      const [{ n }] = await sql<{ n: number }[]>`
        select count(*)::int as n
          from public.newsletter_subscribers
         where ip_hash = ${ipHash}
           and created_at > now() - interval '1 hour'
      `
      if (n >= MAX_PER_HOUR) {
        return NextResponse.json(
          { error: 'Too many signups from here. Try again in an hour.' },
          { status: 429 }
        )
      }
    }

    // A second signup updates where they came from; it never creates a second
    // row. A list with the same person twice mails them twice. Re-subscribing
    // also clears an earlier opt-out, which is what asking again means.
    await sql`
      insert into public.newsletter_subscribers (email, source, ip_hash)
      values (${email}, ${source}, ${ipHash})
      on conflict (email) do update
        set source = excluded.source,
            unsubscribed_at = null
    `

    return NextResponse.json({
      ok: true,
      // Said by the server as well as printed by the form, so the claim has one
      // source. Nothing sends yet, and the reader is told so.
      message: 'You are on the list. Nothing is being sent yet — the first issue will say so.',
    })
  } catch (err) {
    console.error('newsletter signup failed', err)
    return NextResponse.json({ error: 'Could not save that. Try again.' }, { status: 500 })
  }
}
