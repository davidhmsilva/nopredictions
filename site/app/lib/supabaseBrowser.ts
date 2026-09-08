'use client'

/** The Supabase client for CLIENT components.
 *
 *  It is its own file for a hard reason, not a stylistic one: the server
 *  client imports `next/headers`, and any module that does can never be pulled
 *  into a `'use client'` graph — the build fails outright. Keeping the two in
 *  one file worked until the first client component imported it. See
 *  `supabaseAuth.ts` for the server half.
 *
 *  The session lives in cookies, so this client and the server one see the
 *  same thing. That is the whole point of `@supabase/ssr`.
 */

import { createBrowserClient } from '@supabase/ssr'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './supabaseEnv'

export function supabaseBrowser() {
  return createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY)
}
