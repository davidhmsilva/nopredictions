'use client'

import { useEffect, useRef, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { AppNav, AppFooter } from '../../components/AppShell'
import { WalletReport } from '../../components/WalletReport'
import type { WalletProfile } from '../../lib/wallet'

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
  const [stage, setStage] = useState(0)
  const started = useRef(0)
  const [elapsed, setElapsed] = useState(0)


  useEffect(() => {
    if (!address) return
    let cancelled = false
    started.current = Date.now()
    setProfile(null)
    setError(null)

    const tick = setInterval(() => {
      const secs = Math.floor((Date.now() - started.current) / 1000)
      setElapsed(secs)
      setStage(Math.min(STAGES.length - 1, Math.floor(secs / 6)))
    }, 1000)

    fetch(`/api/wallet?address=${address}`)
      .then(async (r) => {
        const body = await r.json()
        if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`)
        return body as WalletProfile
      })
      .then((p) => !cancelled && setProfile(p))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Unknown error'))
      .finally(() => clearInterval(tick))

    return () => {
      cancelled = true
      clearInterval(tick)
    }
  }, [address])

  return (
    <div className="scanner-page">
      <AppNav />

      <main className="scanner-main">
        <button className="wallet-back" onClick={() => router.push('/wallet')}>
          ← ANALYSE ANOTHER WALLET
        </button>

        {!profile && !error && (
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

        {profile && <WalletReport p={profile} />}
      </main>


      <AppFooter />
    </div>
  )
}
