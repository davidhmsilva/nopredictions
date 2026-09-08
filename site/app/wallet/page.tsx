'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { AppShell } from '../components/AppShell'
import { ToolChips, ToolFacts, ToolForm, ToolHead, ToolOutput } from '../components/ToolPage'
import { QuotaStrip } from '../components/QuotaStrip'
import { useSession } from '../lib/useSession'

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

/** The same shape the Lab's stat row has, and for the same reason: a page
 *  asking for a wallet address should say what it is about to do with it. Every
 *  one of these is a real property of `lib/wallet.ts`, not a boast — the fill
 *  count is the largest account rebuilt so far, and the six traps are the ones
 *  documented in that file. */
const FACTS: { v: string; k: string }[] = [
  { v: 'FIFO', k: 'round trips, every fill' },
  { v: '16,157', k: 'fills on the largest wallet read' },
  { v: '6', k: 'feed traps handled' },
  { v: 'Bootstrap', k: 'clustered by event' },
]

export default function WalletIndexPage() {
  const router = useRouter()
  const [value, setValue] = useState('')
  const { me } = useSession()

  const raw = value.trim()
  // Accept a pasted profile URL as readily as a bare address — that is how
  // anyone actually arrives at a wallet they want to look at.
  const addr = raw.match(/0x[0-9a-fA-F]{40}/)?.[0] || raw
  const valid = ADDRESS.test(addr)

  return (
    <AppShell>
      <div className="tp-page">
        <ToolHead eyebrow="WALLET ANALYSER" title="See how a winning trader actually trades.">
          Paste any Polymarket wallet. We rebuild every fill it has ever made into completed
          positions and tell you what it really does — what it buys, when, where the profit
          genuinely comes from, and whether the record holds up or is one lucky run.
        </ToolHead>

        <QuotaStrip
          quota={me?.wallet ?? null}
          signedIn={Boolean(me?.user)}
          feature="wallet read"
          next="/wallet"
        />

        <ToolForm
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) router.push(`/wallet/${addr.toLowerCase()}`)
          }}
          cta="ANALYSE"
          disabled={!valid}
        >
          <input
            type="text"
            className="tp-input"
            placeholder="0x… or https://polymarket.com/profile/0x…"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Wallet address"
          />
        </ToolForm>

        {raw && !valid && (
          <div className="tp-warn">
            That is not a wallet address — expected 0x followed by 40 hex characters.
          </div>
        )}

        <ToolChips label="ALREADY STUDIED">
          {KNOWN.map((k) => (
            <button
              key={k.addr}
              className="tp-chip is-two-line"
              onClick={() => router.push(`/wallet/${k.addr}`)}
            >
              <strong>{k.label}</strong>
              <span>{k.note}</span>
            </button>
          ))}
        </ToolChips>

        <section className="tp-explain">
          <ToolFacts facts={FACTS} />
          <ToolOutput rows={REPORT_SECTIONS.map((r) => ({ k: r.k, v: r.v }))} />
        </section>
      </div>
    </AppShell>
  )
}
