/** Where Supabase sends the browser back after an OAuth round trip, a magic
 *  link, a confirmation or a password reset.
 *
 *  Supabase answers in one of two shapes, and this route has to survive both:
 *
 *    ?code=…            the PKCE flow. Exchanged for a session here, on the
 *                       server, which is where the session cookie belongs.
 *    #access_token=…    the implicit flow — what the recovery link actually
 *                       sent on 2026-09-19. A fragment never reaches a server,
 *                       so this route saw no `code` and answered
 *                       "missing_code" to someone holding a link that had just
 *                       verified fine. It now hands those over to /auth/finish,
 *                       which reads the fragment in the browser. A redirect
 *                       keeps the fragment: the browser reapplies it when the
 *                       destination has none of its own.
 *
 *  ⚠️ `next` is attacker-controllable — it arrives in a URL anyone can build.
 *  Only a same-site PATH is ever honoured, so this cannot be turned into an
 *  open redirect that borrows the site's name to send someone elsewhere.
 */

import { NextResponse, type NextRequest } from 'next/server'
import { supabaseServer } from '../../lib/supabaseAuth'

export const dynamic = 'force-dynamic'

function safeNext(raw: string | null): string {
  if (!raw) return '/'
  // A path, not a URL. Rejects "//evil.com" and "https://evil.com" alike.
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const next = safeNext(searchParams.get('next'))

  // Supabase can also refuse in the query: an expired or already-used link.
  const err = searchParams.get('error_description') ?? searchParams.get('error')
  if (err) {
    return NextResponse.redirect(`${origin}/login?error=${encodeURIComponent(err)}`)
  }

  if (!code) {
    // Either an implicit-flow session or an error, both in the fragment.
    return NextResponse.redirect(`${origin}/auth/finish?next=${encodeURIComponent(next)}`)
  }

  const { error } = await supabaseServer().auth.exchangeCodeForSession(code)
  if (error) {
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`
    )
  }
  return NextResponse.redirect(`${origin}${next}`)
}
