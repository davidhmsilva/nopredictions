/** Refreshes the auth session on every request that could render a page.
 *
 *  Supabase access tokens are short-lived. Without this, a signed-in user is
 *  signed in until their token expires and then silently is not — the classic
 *  shape of "it logged me out overnight". The refresh has to happen somewhere
 *  that can WRITE cookies, which a server component cannot; middleware can.
 *
 *  It runs on page routes only. The matcher excludes /api deliberately: the
 *  route handlers there read the session themselves, and paying an auth round
 *  trip in front of /api/scout — which is public, cached, and on the tape of
 *  every page — would put the auth server on the critical path of the board.
 */

import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './app/lib/supabaseEnv'

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request })

  // Not configured is not an error: the site works without accounts, and it
  // must keep working if the keys are ever missing from an environment.
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return response

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list) => {
        list.forEach(({ name, value }) => request.cookies.set(name, value))
        response = NextResponse.next({ request })
        list.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options)
        )
      },
    },
  })

  // The call itself is the refresh — its return value is not needed here.
  await supabase.auth.getUser()
  return response
}

export const config = {
  matcher: [
    /*
     * Everything except:
     *   api        — handlers read the session themselves; see above
     *   _next/*    — build output
     *   *.png|...  — static assets
     */
    '/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|ico|webp)$).*)',
  ],
}
