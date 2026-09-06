'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState, type FormEvent } from 'react'

/** The tabs, in the order a bettor uses them on a matchday:
 *  where is the edge → can I test my own idea → what is the agent doing →
 *  who else is doing it well. Game Center is deliberately absent: you reach a
 *  fixture by clicking it, never by picking a tab. */
export const TABS: { href: string; label: string; hint: string }[] = [
  { href: '/',       label: 'Scout',  hint: "Today's boards" },
  { href: '/lab',    label: 'Lab',    hint: 'Test a theory' },
  { href: '/agent',  label: 'Agent',  hint: 'In testing' },
  { href: '/wallet', label: 'Wallet', hint: 'Read a trader' },
]

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/' || pathname.startsWith('/game')
  return pathname === href || pathname.startsWith(href + '/')
}

// ── the tape ─────────────────────────────────────────────────────────────────

interface Pulse {
  boards: number
  markets: number
  competitions: number
  live: number
  clean: number
  volumeUsd: number
  liquidityUsd: number
}

function money(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

/** Every number here is counted off the live board, not typed in. It runs on
 *  every page and shares the board cache with the table, so a page that shows
 *  both pays for one sweep. */
function Tape() {
  const [p, setPulse] = useState<Pulse | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/pulse')
      .then((r) => r.json())
      .then((b) => {
        if (!cancelled && b.ok) setPulse(b)
      })
      .catch(() => {
        /* the tape is decoration; a page without it still works */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const cells: { label: string; value: string; cls?: string }[] = p
    ? [
        { label: 'FOOTBALL BOARDS', value: String(p.boards) },
        { label: 'MARKETS', value: p.markets.toLocaleString('en-US') },
        { label: 'COMPETITIONS', value: String(p.competitions) },
        { label: 'IN PLAY', value: String(p.live), cls: 'is-live' },
        { label: 'CLEAN BOOKS', value: String(p.clean), cls: 'is-good' },
        {
          label: 'CLEAN SHARE',
          value: p.boards ? `${Math.round((p.clean / p.boards) * 100)}%` : '—',
          cls: 'is-good',
        },
        { label: 'VOLUME', value: money(p.volumeUsd) },
        { label: 'LIQUIDITY', value: money(p.liquidityUsd) },
        { label: 'VENUE', value: 'POLYMARKET' },
      ]
    : []

  if (!p) {
    return (
      <div className="np-tape">
        <div className="np-tape-static">
          <span className="np-tape-cell">READING THE BOARD…</span>
        </div>
      </div>
    )
  }

  // The list is rendered twice so the marquee can loop without a gap. The
  // duplicate is hidden from assistive tech rather than read out again.
  const strip = (dup: boolean) => (
    <div className="np-tape-strip" aria-hidden={dup || undefined}>
      {cells.map((c) => (
        <span key={c.label} className={`np-tape-cell ${c.cls ?? ''}`}>
          <b className="np-num">{c.value}</b> {c.label}
        </span>
      ))}
    </div>
  )

  return (
    <div className="np-tape">
      <div className="np-tape-track">
        {strip(false)}
        {strip(true)}
      </div>
    </div>
  )
}

// ── nav ──────────────────────────────────────────────────────────────────────

function NavSearch() {
  const router = useRouter()
  const [q, setQ] = useState('')

  /** A pasted Polymarket link goes straight to that fixture; anything else is a
   *  team search on the board. Those are the only two things anyone types here,
   *  and guessing wrong just lands them on the board with the text in the box. */
  function submit(e: FormEvent) {
    e.preventDefault()
    const v = q.trim()
    if (!v) return
    const slug = v.match(
      /polymarket\.com\/(?:[a-z]{2}\/)?(?:event|sports\/[^/]+)\/([^/?#]+)/
    )?.[1]
    router.push(slug ? `/game/${slug}` : `/?q=${encodeURIComponent(v)}`)
  }

  return (
    <form className="np-search" onSubmit={submit} role="search">
      <span className="np-search-icn" aria-hidden="true">⌕</span>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search a team, or paste a Polymarket link…"
        aria-label="Search fixtures"
      />
    </form>
  )
}

export function AppNav() {
  const pathname = usePathname() ?? '/'

  return (
    <>
      <Tape />

      <header className="np-nav">
        <div className="np-nav-inner">
          <Link href="/" className="np-brand">
            <span className="np-brand-mark" aria-hidden="true" />
            <span className="np-brand-text">
              <span className="np-brand-name">NOPREDICTIONS</span>
              <span className="np-brand-sub">No predictions. Just edges.</span>
            </span>
          </Link>

          <nav className="np-tabs" aria-label="Main">
            {TABS.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={`np-tab${isActive(pathname, t.href) ? ' is-active' : ''}`}
                aria-current={isActive(pathname, t.href) ? 'page' : undefined}
              >
                {t.label}
                {t.href === '/agent' && <span className="np-tab-flag">TESTING</span>}
              </Link>
            ))}
          </nav>

          <div className="np-nav-right">
            <NavSearch />
            <span
              className="np-paper-pill"
              title="No real money is at risk anywhere on this site. There are no accounts, because there is nothing yet to sign in to."
            >
              PAPER MODE
            </span>
          </div>
        </div>
      </header>

      <nav className="np-mobile-tabs" aria-label="Main">
        {TABS.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            className={`np-mobile-tab${isActive(pathname, t.href) ? ' is-active' : ''}`}
          >
            <span className="np-mobile-tab-label">{t.label}</span>
            <span className="np-mobile-tab-hint">{t.hint}</span>
          </Link>
        ))}
      </nav>
    </>
  )
}

export function AppFooter() {
  return (
    <footer className="np-footer">
      <div className="np-footer-inner">
        <div className="np-footer-brand">NOPREDICTIONS</div>
        <div className="np-footer-note">
          Every number on this site is measured, and the ones that are not measured say so.
          Nothing here is a tip.
        </div>
        <div className="np-footer-legal">
          18+ · NOT FINANCIAL OR BETTING ADVICE · PAST RESULTS DO NOT PREDICT FUTURE RESULTS
        </div>
      </div>
    </footer>
  )
}

/** Page chrome. Every route renders inside this so the tabs never move. */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="np-app">
      <AppNav />
      <main className="np-main">{children}</main>
      <AppFooter />
    </div>
  )
}
