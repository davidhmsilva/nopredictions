'use client'

/** The browser half of /auth/callback.
 *
 *  Supabase's implicit flow returns the session in the URL FRAGMENT
 *  (`#access_token=…&refresh_token=…`), which no server ever receives — the
 *  password-reset link did exactly that on 2026-09-19 and the callback could
 *  only answer "missing_code". This page reads the fragment, hands the tokens
 *  to the Supabase client (which writes the same cookies the server half
 *  writes), strips them out of the address bar, and sends the person on.
 *
 *  The fragment can also carry a refusal — a link already used, or expired —
 *  and that is said plainly rather than as a code.
 */

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { supabaseBrowser } from '../../lib/supabaseBrowser'
import { invalidateSession } from '../../lib/useSession'

function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

function Finish() {
  const router = useRouter()
  const params = useSearchParams()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const next = safeNext(params.get('next'))
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const access_token = hash.get('access_token')
    const refresh_token = hash.get('refresh_token')
    const refused = hash.get('error_description') ?? hash.get('error')

    if (refused) {
      setError(refused.replace(/\+/g, ' '))
      return
    }
    if (!access_token || !refresh_token) {
      setError('This link carried no session. It has probably been used already, or it expired.')
      return
    }

    ;(async () => {
      const { error: err } = await supabaseBrowser().auth.setSession({ access_token, refresh_token })
      if (err) {
        setError(err.message)
        return
      }
      invalidateSession()
      // Drop the tokens from the address bar before anything can copy the URL.
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
      router.replace(next)
      router.refresh()
    })()
  }, [params, router])

  return (
    <div className="lg-page">
      <div className="lg-card">
        <h1 className="lg-card-h1">{error ? 'That link did not work' : 'Signing you in…'}</h1>
        {error ? (
          <>
            <p className="lg-sub">{error}</p>
            <Link href="/login" className="np-btn np-btn-primary lg-wide">
              Ask for a new one
            </Link>
          </>
        ) : (
          <p className="lg-sub">One moment.</p>
        )}
      </div>
    </div>
  )
}

export default function AuthFinishPage() {
  return (
    <AppShell>
      <Suspense fallback={<div className="lg-page" />}>
        <Finish />
      </Suspense>
    </AppShell>
  )
}
