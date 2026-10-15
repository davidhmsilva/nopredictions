'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { AppShell } from '../components/AppShell'
import { ToolHead } from '../components/ToolPage'
import { QuotaStrip } from '../components/QuotaStrip'
import { useSession } from '../lib/useSession'
import {
  LEADER_PERIODS,
  LEADERS_SHOWN,
  STUDIED,
  displayName,
  shortMoney,
  type Leader,
  type LeaderPeriod,
  type ProfileHit,
} from '../lib/walletTerms'

// The Wallet tab used to open on a box asking for a 42-character address and
// a list of what the report computes. Nobody arrives knowing an address, and
// the five shortcuts all hit a sign-in wall. It now starts from people: the
// sports leaderboard and the traders we wrote up open free, and any name can
// be searched.

const ADDRESS = /0x[0-9a-fA-F]{40}/

function Avatar({ name, image, size = 40 }: { name: string; image: string; size?: number }) {
  const [broken, setBroken] = useState(false)
  if (image && !broken) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={image}
        alt=""
        className="wx-avatar"
        style={{ width: size, height: size }}
        loading="lazy"
        onError={() => setBroken(true)}
      />
    )
  }
  const letter = (name.replace(/^0x/i, '')[0] || '?').toUpperCase()
  return (
    <span className="wx-avatar is-blank" style={{ width: size, height: size }} aria-hidden="true">
      {letter}
    </span>
  )
}

function Search() {
  const router = useRouter()
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<ProfileHit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [searching, setSearching] = useState(false)
  const [miss, setMiss] = useState(false)
  const seq = useRef(0)

  const addr = q.match(ADDRESS)?.[0]?.toLowerCase() ?? null

  useEffect(() => {
    const term = q.trim()
    setMiss(false)
    if (addr || term.length < 2) {
      setHits([])
      return
    }
    const id = ++seq.current
    const t = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await fetch(`/api/wallet/search?q=${encodeURIComponent(term)}`)
        const d = await r.json()
        if (id !== seq.current) return
        setHits(d.profiles ?? [])
        setActive(-1)
        setMiss((d.profiles ?? []).length === 0)
        setOpen(true)
      } catch {
        if (id === seq.current) setHits([])
      } finally {
        if (id === seq.current) setSearching(false)
      }
    }, 250)
    return () => clearTimeout(t)
  }, [q, addr])

  function go(address: string) {
    setOpen(false)
    router.push(`/wallet/${address}`)
  }

  function submit() {
    if (addr) return go(addr)
    const pick = hits[active >= 0 ? active : 0]
    if (pick) go(pick.address)
  }

  return (
    <div className="wx-search">
      <form
        className="tp-form"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <input
          type="text"
          className="tp-input"
          placeholder="Search a trader's name, or paste a profile link"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActive((a) => Math.min(hits.length - 1, a + 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((a) => Math.max(-1, a - 1))
            } else if (e.key === 'Escape') {
              setOpen(false)
            }
          }}
          aria-label="Trader name or wallet address"
          aria-autocomplete="list"
          aria-expanded={open && hits.length > 0}
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" className="tp-go" disabled={!addr && hits.length === 0}>
          {searching ? 'Searching…' : 'Look up'}
        </button>
      </form>
      {open && hits.length > 0 && (
        <ul className="wx-hits" role="listbox">
          {hits.map((h, i) => (
            <li key={h.address} role="option" aria-selected={i === active}>
              <button
                type="button"
                className={`wx-hit${i === active ? ' is-on' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(h.address)}
              >
                <Avatar name={h.name || h.address} image={h.image} size={28} />
                <span className="wx-hit-name">{displayName(h.name, h.address)}</span>
                <span className="wx-hit-addr np-num">
                  {h.address.slice(0, 6)}…{h.address.slice(-4)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {miss && !addr && <div className="wx-miss">No trader by that name. Try part of it, or paste their profile link.</div>}
    </div>
  )
}

function Board() {
  const [period, setPeriod] = useState<LeaderPeriod>('month')
  const [data, setData] = useState<Partial<Record<LeaderPeriod, Leader[]>>>({})
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (data[period]) return
    let cancelled = false
    setFailed(false)
    fetch(`/api/wallet/leaders?period=${period}`)
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return
        if (d.ok) setData((prev) => ({ ...prev, [period]: d.leaders }))
        else setFailed(true)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
    }
  }, [period, data])

  const rows = data[period]

  return (
    <section className="wx-board">
      <div className="wx-board-head">
        <h2>Top sports traders</h2>
        <div className="wx-tabs" role="tablist">
          {LEADER_PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={period === p.id}
              className={`wx-tab${period === p.id ? ' is-on' : ''}`}
              onClick={() => setPeriod(p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {failed ? (
        <div className="tp-warn">Polymarket&apos;s leaderboard did not answer. Search a name above instead.</div>
      ) : (
        <div className="wx-grid">
          {rows
            ? rows.map((l) => (
                <Link key={l.address} href={`/wallet/${l.address}`} className="wx-card">
                  <span className="wx-rank np-num">{l.rank}</span>
                  <Avatar name={l.name || l.address} image={l.image} />
                  <span className="wx-card-body">
                    <span className="wx-card-name">{displayName(l.name, l.address)}</span>
                    <span className="wx-card-profit np-num">
                      {l.profit >= 0 ? '+' : ''}
                      {shortMoney(l.profit)}
                    </span>
                    <span className="wx-card-vol np-num">{shortMoney(l.volume)} traded</span>
                  </span>
                </Link>
              ))
            : Array.from({ length: LEADERS_SHOWN }, (_, i) => (
                <div key={i} className="wx-card is-skeleton" aria-hidden="true" />
              ))}
        </div>
      )}
      <p className="wx-foot">
        Profit as Polymarket reports it, before fees. Open any of them free — our read shows the
        profit after fees, and whether it holds up.
      </p>
    </section>
  )
}

export default function WalletIndexPage() {
  const router = useRouter()
  const { me } = useSession()

  return (
    <AppShell>
      <div className="tp-page wx-page">
        <ToolHead eyebrow="WALLETS" title="See how Polymarket's best traders actually trade.">
          Pick a trader or search any name. We rebuild every bet they have made and show what they
          buy, when, where the money really comes from — and whether it is skill or one lucky run.
        </ToolHead>

        <Search />
        <div className="wx-quota">
          <QuotaStrip quota={me?.wallet ?? null} signedIn={Boolean(me?.user)} feature="wallet read" next="/wallet" />
          <span className="wx-quota-note">The traders on this page are free. Any other wallet counts as a read.</span>
        </div>

        <Board />

        <section className="wx-studied">
          <h2>Traders we&apos;ve written up</h2>
          <div className="tp-chips-row">
            {STUDIED.map((k) => (
              <button key={k.addr} type="button" className="tp-chip is-two-line" onClick={() => router.push(`/wallet/${k.addr}`)}>
                <strong>{k.label}</strong>
                <span>{k.note}</span>
              </button>
            ))}
          </div>
        </section>
      </div>
    </AppShell>
  )
}
