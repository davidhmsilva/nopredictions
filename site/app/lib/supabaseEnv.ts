/** The Supabase URL and anon key, in one place, with the fallback that kept
 *  the site up.
 *
 *  ⚠️ `NEXT_PUBLIC_SUPABASE_URL` was NOT set on Vercel — only the anon key was
 *     (checked 2026-09-08: five production variables, and that was not one of
 *     them). Nothing broke, because `supabase.ts` carried the URL as a literal
 *     default. Auth would have broken loudly, because it read the variable and
 *     asserted it with `!`.
 *
 *  So both halves are fixed: the variable is set on Vercel, AND the fallback
 *  lives here where every caller shares it rather than in one file that
 *  happened to have it. Neither value is a secret — the anon key ships inside
 *  the client bundle by design, and RLS is what protects the data (db/044,
 *  db/045). A literal here leaks nothing that `view-source` does not.
 *
 *  Deliberately tiny and dependency-free: it is imported by the middleware,
 *  which runs on every page request and should not pull in a Supabase client
 *  to read two strings.
 */

export const SUPABASE_URL =
  process.env.NEXT_PUBLIC_SUPABASE_URL ?? 'https://pnpjyrvvbzsdrwtymyzz.supabase.co'

export const SUPABASE_ANON_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucGp5cnZ2YnpzZHJ3dHlteXp6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY3ODk0ODcsImV4cCI6MjA5MjM2NTQ4N30.RWa6hRDHc3QKHU5TOwKRnJ75FmRP6iJMG2CaEPTrnso'
