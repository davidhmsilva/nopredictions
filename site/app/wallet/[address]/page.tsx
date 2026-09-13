'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { AppShell } from '../../components/AppShell'
import { WalletReport } from '../../components/WalletReport'
import { WalletContents } from '../../components/WalletContents'
import type { WalletProfile } from '../../lib/wallet'
import { QUOTA_RESET_TEXT } from '../../lib/planTerms'

// A big wallet is tens of thousands of fills; the request runs for a while and
// a bare spinner for 20 seconds reads as a hang. These say what is happening.
const STAGES = [
  'Walking the activity feed…',
  'Still walking — a busy wallet is tens of thousands of fills…',
  'Fetching market metadata…',
  'Matching FIFO round trips…',
  'Bootstrapping the confidence interval…',
  'Almost there…',
]

export default function WalletPage() {
  const params = useParams<{ address: string }>()
  const router = useRouter()
  const address = String(params?.address || '')
  const [profile, setProfile] = useState<WalletProfile | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 401 = no account, 402 = the day is spent. Neither is a failure to analyse
  // the wallet, and showing them under "Could not analyse this wallet" would
  // blame the address for a decision about the plan.
  const [gate, setGate] = useState<'signed_out' | 'quota' | null>(null)
  const [stage, setStage] = useState(0)
  const started = useRef(0)
  const [elapsed, setElapsed] = useState(0)


  useEffect(() => {
    if (!address) return
    let cancelled = false
    started.current = Date.now()
    setProfile(null)
    setError(null)
    setGate(null)

    const tick = setInterval(() => {
      const secs = Math.floor((Date.now() - started.current) / 1000)
      setElapsed(secs)
      setStage(Math.min(STAGES.length - 1, Math.floor(secs / 6)))
    }, 1000)

    fetch(`/api/wallet?address=${address}`)
      .then(async (r) => {
        const body = await r.json()
        if (r.status === 401 || r.status === 402) {
          if (!cancelled) setGate(r.status === 401 ? 'signed_out' : 'quota')
          return null
        }
        if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`)
        return body as WalletProfile
      })
      .then((p) => { if (p && !cancelled) setProfile(p) })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Unknown error'))
      .finally(() => clearInterval(tick))

    return () => {
      cancelled = true
      clearInterval(tick)
    }
  }, [address])

  return (
    <AppShell>
      <div className="wr-page">
        <button className="wallet-back" onClick={() => router.push('/wallet')}>
          ← ANALYSE ANOTHER WALLET
        </button>

        {gate && (
          <div className="np-card np-wallet-gate">
            <h2>{gate === 'signed_out' ? 'This one needs an account' : "That is today's three"}</h2>
            <p>
              {gate === 'signed_out'
                ? 'Rebuilding a trader’s whole record is the expensive half of this site. A free account gets three a day, and takes an email and a password.'
                : `Free accounts get three wallet reads a day. The count resets at ${QUOTA_RESET_TEXT} — or Pro removes the limit.`}
            </p>
            <div className="np-btn-row">
              {gate === 'signed_out' ? (
                <Link
                  className="np-btn np-btn-primary"
                  href={`/login?next=${encodeURIComponent(`/wallet/${address}`)}`}
                >
                  Sign in — it is free
                </Link>
              ) : (
                <Link className="np-btn np-btn-primary" href="/pricing">See Pro</Link>
              )}
              <Link className="np-btn" href="/wallet">Back</Link>
            </div>
          </div>
        )}

        {!profile && !error && !gate && (
          <div className="wallet-loading">
            <span className="scan-spinner" />
            <div>
              <div className="wallet-loading-stage">{STAGES[stage]}</div>
              <div className="wallet-loading-sub">
                {address.slice(0, 10)}…{address.slice(-6)} · {elapsed}s
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="wallet-error">
            <div className="wallet-error-title">Could not analyse this wallet</div>
            <div className="wallet-error-body">{error}</div>
          </div>
        )}

        {profile && (
          <>
            <WalletContents />
            <WalletReport p={profile} />
          </>
        )}
      </div>
    </AppShell>
  )
}
