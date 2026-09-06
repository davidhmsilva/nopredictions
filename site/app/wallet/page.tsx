'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { AppNav, AppFooter } from '../components/AppShell'

const ADDRESS = /^0x[0-9a-fA-F]{40}$/

/** Benchmark wallets already studied — the shortcuts save retyping 42 hex
 *  characters, and each one is a different archetype to compare against. */
const KNOWN = [
  { addr: '0xec5723df1ef786d95b05b2941c89b45dcb560fa7', label: 'GSX-', note: 'in-play tail-buyer' },
  { addr: '0x2005d16a84ceefa912d4e380cd32e7ff827875ea', label: 'RN1', note: '79% maker, our universe' },
  { addr: '0x204f72f35326db932158cba6adff0b9a1da95e14', label: 'swisstony', note: 'exits by merge' },
  { addr: '0x2c335066fe58fe9237c3d3dc7b275c2a034a0563', label: '0x2c33…', note: '99% maker' },
  { addr: '0xf0318c32136c2db7fec88b84869aee6a1106c80c', label: 'BreakTheBank', note: 'big number, no edge' },
]

export default function WalletIndexPage() {
  const router = useRouter()
  const [value, setValue] = useState('')

  const raw = value.trim()
  // Accept a pasted profile URL as readily as a bare address — that is how
  // anyone actually arrives at a wallet they want to look at.
  const addr = raw.match(/0x[0-9a-fA-F]{40}/)?.[0] || raw
  const valid = ADDRESS.test(addr)

  return (
    <div className="scanner-page">
      <AppNav />

      <main className="scanner-main">
        <div className="scanner-hero">
          <span className="scanner-eyebrow">WALLET ANALYSER</span>
          <h1 className="scanner-h1">Read a trader&rsquo;s whole record.</h1>
          <p className="scanner-hero-sub">
            Paste a Polymarket wallet address or profile link. Every fill it has ever made is
            rebuilt into FIFO round trips — what it buys, when it buys it, where the money
            actually comes from, and how the behaviour changed over time.
          </p>
        </div>

        <section className="scan-section">
          <div className="analyze-input-row">
            <input
              type="text"
              className="analyze-input"
              placeholder="0x… or https://polymarket.com/profile/0x…"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && valid && router.push(`/wallet/${addr.toLowerCase()}`)}
            />
            <button
              className="scan-btn"
              disabled={!valid}
              onClick={() => router.push(`/wallet/${addr.toLowerCase()}`)}
            >
              ANALYSE
            </button>
          </div>
          {raw && !valid && (
            <div className="wallet-inline-warn">
              That is not a wallet address — expected 0x followed by 40 hex characters.
            </div>
          )}

          <div className="wallet-known">
            <div className="wallet-known-label">ALREADY STUDIED</div>
            <div className="wallet-known-row">
              {KNOWN.map((k) => (
                <button key={k.addr} className="wallet-known-chip" onClick={() => router.push(`/wallet/${k.addr}`)}>
                  <strong>{k.label}</strong>
                  <span>{k.note}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </main>


      <AppFooter />
    </div>
  )
}
