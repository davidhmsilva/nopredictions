/** Where Supabase sends the browser back after an OAuth round trip or a
 *  magic-link click. The `code` in the URL is exchanged for a session, which
 *  lands in cookies, and the user goes on to wherever they were headed.
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

  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=missing_code`)
  }

  const { error } = await supabaseServer().auth.exchangeCodeForSession(code)
  if (error) {
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`
    )
  }
  return NextResponse.redirect(`${origin}${next}`)
}
