/** The SERVER half of session-aware Supabase access.
 *
 *  ⚠️ Server only. It imports `next/headers`, and Next refuses to compile any
 *     `'use client'` module that reaches this file — which is exactly what
 *     happened when the browser client lived here too. The browser half is in
 *     `supabaseBrowser.ts`, and the split is load-bearing rather than tidy.
 *
 *  `app/lib/supabase.ts` already exports a plain anon client, and it stays as
 *  it is: it reads public data — strategies, paper trades, the odds tables —
 *  and it has no user. These two are the ones that know who is asking.
 *
 *  🔑 The session lives in cookies, not in localStorage, because a server
 *  component and a route handler have to be able to read it. That is the whole
 *  reason `@supabase/ssr` exists and the reason a middleware is needed at all:
 *  an access token expires, and only a request that can WRITE a cookie can
 *  refresh it. See `middleware.ts`.
 */

import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import { SUPABASE_ANON_KEY as ANON, SUPABASE_URL as URL } from './supabaseEnv'

/** For server components, server actions and route handlers. */
export function supabaseServer() {
  const store = cookies()
  return createServerClient(URL, ANON, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        // A server COMPONENT may not set cookies, and Next throws when it
        // tries. That is not an error worth propagating: the middleware has
        // already refreshed the session on this same request, so the write
        // this throw discards is one that has happened elsewhere.
        try {
          list.forEach(({ name, value, options }) => store.set(name, value, options))
        } catch {
          /* server component — middleware owns the refresh */
        }
      },
    },
  })
}

/** The signed-in user, or null.
 *
 *  ⚠️ `getUser()`, never `getSession()`. `getSession` returns whatever is in
 *  the cookie without asking Supabase whether it is genuine, so it can be
 *  forged; `getUser` validates the token against the auth server. On a page
 *  that decides what someone may do, that difference is the whole point. */
export async function currentUser() {
  const { data, error } = await supabaseServer().auth.getUser()
  if (error) return null
  return data.user ?? null
}
