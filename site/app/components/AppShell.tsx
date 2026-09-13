'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState, type FormEvent } from 'react'
import {
  IconAccount,
  IconAgent,
  IconBoard,
  IconLab,
  IconMenu,
  IconSearch,
  IconWallet,
} from './icons'
import { invalidateSession, useSession } from '../lib/useSession'

/** The tabs, in the order a bettor uses them on a matchday:
 *  where is the edge → can I test my own idea → what is the agent doing →
 *  who else is doing it well. Game Center is deliberately absent: you reach a
 *  fixture by clicking it, never by picking a tab. */
export const TABS: {
  href: string
  label: string
  hint: string
  Icon: (p: { className?: string }) => JSX.Element
}[] = [
  { href: '/',       label: 'Scout',  hint: "Today's boards", Icon: IconBoard },
  { href: '/lab',    label: 'Lab',    hint: 'Test a theory',  Icon: IconLab },
  { href: '/agent',  label: 'Agents', hint: 'Yours',          Icon: IconAgent },
  { href: '/wallet', label: 'Wallet', hint: 'Read a trader',  Icon: IconWallet },
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

function NavSearch({
  onDone,
  autoFocus,
}: {
  onDone?: () => void
  autoFocus?: boolean
} = {}) {
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
    onDone?.()
  }

  return (
    <form className="np-search" onSubmit={submit} role="search">
      <span className="np-search-icn" aria-hidden="true">⌕</span>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search a team, or paste a Polymarket link…"
        aria-label="Search fixtures"
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus={autoFocus}
      />
    </form>
  )
}

/** Who is signed in, and the controls that change it.
 *
 *  These were rendered but inert until accounts existed — a click said so
 *  rather than swallowing itself. They are wired now, and the only thing that
 *  did NOT survive the wiring is the bell: alerts have a table (db/044) and no
 *  delivery, and a bell that opens nothing is the newsletter form this site
 *  already deleted once.
 */
function AccountActions() {
  const { me, loading } = useSession()
  const [sheet, setSheet] = useState(false)
  const [mobileSearch, setMobileSearch] = useState(false)
  const pathname = usePathname() ?? '/'
  const router = useRouter()

  useEffect(() => {
    if (!sheet) return
    const close = () => setSheet(false)
    window.addEventListener('resize', close)
    return () => window.removeEventListener('resize', close)
  }, [sheet])

  // Coming back to where you were is the difference between a sign-in and an
  // interruption. `/login` and `/account` are excluded: returning to the login
  // page after logging in is a loop.
  const next =
    pathname.startsWith('/login') || pathname.startsWith('/account')
      ? '/'
      : pathname
  const loginHref = `/login?next=${encodeURIComponent(next)}`

  async function signOut() {
    setSheet(false)
    // ⚠️ Imported here, not at the top of the file. This header renders on
    //    every page, so a static import pulls the whole Supabase client into
    //    the shared bundle — measured at +70kB of First Load JS on the board,
    //    paid by every visitor to carry a button most of them never press.
    //    Signing out can afford a round trip; opening the board cannot.
    const { supabaseBrowser } = await import('../lib/supabaseBrowser')
    await supabaseBrowser().auth.signOut()
    invalidateSession()
    router.push('/')
    router.refresh()
  }

  const signedIn = Boolean(me?.user)
  const isPro = me?.plan === 'pro'
  // Before the first answer lands, render the signed-out shape rather than a
  // spinner: it is right for most visitors and it does not shift the layout
  // when it resolves.
  const showAccount = !loading && signedIn

  return (
    <div className="np-account">
      {/* Wide screens. */}
      {showAccount ? (
        <>
          {!isPro && (
            <Link href="/pricing" className="np-btn-ghost np-wide-only">Pro</Link>
          )}
          <Link href="/account" className="np-btn-signup np-wide-only" title={me?.user?.email ?? undefined}>
            {isPro ? 'PRO' : 'Account'}
          </Link>
        </>
      ) : (
        <>
          <Link href="/pricing" className="np-btn-ghost np-wide-only">Pricing</Link>
          <Link href={loginHref} className="np-btn-ghost np-wide-only">Log in</Link>
          <Link href={loginHref} className="np-btn-signup np-wide-only">Sign up</Link>
        </>
      )}

      {/* Phones: search, account, menu — the three that fit beside a logo. */}
      <button
        className="np-icon-btn np-narrow-only"
        onClick={() => setMobileSearch((v) => !v)}
        aria-label="Search"
        aria-expanded={mobileSearch}
      >
        <IconSearch className="np-icn" />
      </button>
      <Link
        className="np-icon-btn np-narrow-only"
        href={showAccount ? '/account' : loginHref}
        aria-label={showAccount ? 'Your account' : 'Sign in'}
      >
        <IconAccount className="np-icn" />
      </Link>
      <button
        className="np-icon-btn np-narrow-only"
        onClick={() => setSheet((v) => !v)}
        aria-label="Menu"
        aria-expanded={sheet}
      >
        <IconMenu className="np-icn" />
      </button>

      {mobileSearch && (
        <div className="np-mobile-search">
          <NavSearch onDone={() => setMobileSearch(false)} autoFocus />
        </div>
      )}

      {sheet && (
        <>
          <button className="np-sheet-scrim" aria-label="Close menu" onClick={() => setSheet(false)} />
          <div className="np-sheet" role="dialog" aria-label="Menu">
            {showAccount ? (
              <>
                <Link className="np-sheet-item" href="/account" onClick={() => setSheet(false)}>
                  Account
                </Link>
                {!isPro && (
                  <Link className="np-sheet-item is-primary" href="/pricing" onClick={() => setSheet(false)}>
                    Upgrade to Pro
                  </Link>
                )}
                <button className="np-sheet-item" onClick={signOut}>Sign out</button>
                <div className="np-sheet-note">{me?.user?.email}</div>
              </>
            ) : (
              <>
                <Link className="np-sheet-item" href={loginHref} onClick={() => setSheet(false)}>
                  Log in
                </Link>
                <Link className="np-sheet-item is-primary" href={loginHref} onClick={() => setSheet(false)}>
                  Sign up
                </Link>
                <Link className="np-sheet-item" href="/pricing" onClick={() => setSheet(false)}>
                  Pricing
                </Link>
                <div className="np-sheet-note">
                  Scout, Agent and the Game Center work without an account.
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
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
                {/* The TESTING flag used to sit here. It labelled the paper
                    record, and since 2026-09-09 that record lives at
                    /agent/ours — /agent is the empty state, and flagging "you
                    do not have an agent" as in testing says nothing. The IN
                    TESTING banner is still on the record itself, which is the
                    thing it was ever about. */}
              </Link>
            ))}
          </nav>

          <div className="np-nav-right">
            <NavSearch />
            <AccountActions />
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
            <t.Icon className="np-mobile-tab-icn" />
            <span className="np-mobile-tab-label">{t.label}</span>
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
          PAPER ONLY — NO REAL MONEY · 18+ · NOT FINANCIAL OR BETTING ADVICE · PAST RESULTS DO
          NOT PREDICT FUTURE RESULTS
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
