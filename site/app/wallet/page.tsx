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

/** What the report actually contains. Same reason as the Lab's version: a
 *  visitor who has never seen one has no idea what "FIFO round trips" buys
 *  them, and the list is more convincing than the phrase. Every line here is
 *  something the analyser computes — nothing aspirational. */
const REPORT_SECTIONS: { k: string; v: string }[] = [
  {
    k: 'Every round trip',
    v: 'Each fill matched off FIFO into completed positions — entry, exit, hold time, what it made.',
  },
  {
    k: 'Where the money is',
    v: 'Which leg of the book carries the profit. On the wallet that started our own research, one leg was +783% and the rest was flat.',
  },
  {
    k: 'How it changed',
    v: 'Month by month, with the regime change named when the behaviour shifts.',
  },
  {
    k: 'Does it survive',
    v: 'A bootstrap clustered by event, so one lucky tournament cannot carry the record.',
  },
  {
    k: 'A written reading',
    v: 'The archetype it matches and why, in sentences — each one a threshold on a number in the profile.',
  },
  {
    k: 'What is not established',
    v: 'Said outright, plus a trust flag when the walk never reached the start of the account.',
  },
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
          <h1 className="scanner-h1">See how a winning trader actually trades.</h1>
          <p className="scanner-hero-sub">
            Paste any Polymarket wallet. We rebuild every fill it has ever made into completed
            positions and tell you what it really does — what it buys, when, where the profit
            genuinely comes from, and whether the record holds up or is one lucky run.
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

          <div className="wl-what">
            <div className="wl-what-head">What comes back</div>
            <dl className="bt-out">
              {REPORT_SECTIONS.map((r) => (
                <div key={r.k} className="bt-out-row">
                  <dt>{r.k}</dt>
                  <dd>{r.v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </main>


      <AppFooter />
    </div>
  )
}
