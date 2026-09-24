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
import { OddsToggle } from './OddsToggle'
import { SPORT_KEYS } from '../lib/sportsMeta'

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
  { href: '/',       label: 'Home',   hint: "Today's boards", Icon: IconBoard },
  { href: '/lab',    label: 'Lab',    hint: 'Test a theory',  Icon: IconLab },
  { href: '/agent',  label: 'Agents', hint: 'Yours',          Icon: IconAgent },
  { href: '/wallet', label: 'Wallet', hint: 'Read a trader',  Icon: IconWallet },
]

function isActive(pathname: string, href: string): boolean {
  // Home owns every board: the top of soccer at "/", all of it at /soccer,
  // and each US sport at its own path.
  if (href === '/') {
    return (
      pathname === '/' ||
      pathname === '/soccer' ||
      pathname.startsWith('/game') ||
      SPORT_KEYS.some((k) => pathname === `/${k}` || pathname.startsWith(`/${k}/`))
    )
  }
  return pathname === href || pathname.startsWith(href + '/')
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

  /** A pasted Polymarket soccer link goes straight to that fixture; anything
   *  else — a team in any sport, or a link to a US game — is a search on the
   *  home board, which holds every sport and matches full names, short names,
   *  abbreviations and each game's exchange links. */
  function submit(e: FormEvent) {
    e.preventDefault()
    const v = q.trim()
    if (!v) return
    const slug = v.match(
      /polymarket\.com\/(?:[a-z]{2}\/)?(?:event|sports\/[^/]+)\/([^/?#]+)/
    )?.[1]
    // US games on Polymarket are slugged by league ("nfl-nyg-la-…"); the
    // soccer Game Center cannot open those, the board search can.
    const usSlug = slug && /^(nfl|cfb|mlb|nba|nhl|wnba)-/.test(slug)
    const term = usSlug ? (slug as string) : v
    router.push(slug && !usSlug ? `/game/${slug}` : `/?q=${encodeURIComponent(term)}`)
    // Already on the home page, a push to "/?q=" does not remount it; tell it.
    if (!(slug && !usSlug)) window.dispatchEvent(new CustomEvent('np-search', { detail: term }))
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
  // Every "Sign up" opened the sign-IN form: /login defaults to it unless
  // told otherwise, so a new visitor pressing the green button met a form
  // for an account they did not have.
  const signupHref = `/login?mode=signup&next=${encodeURIComponent(next)}`

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
          <Link href={signupHref} className="np-btn-signup np-wide-only">Sign up</Link>
        </>
      )}

      {/* Phones: search, then who you are, then the menu. Signed out, "who
          you are" is a Sign up button, not a person icon: the icon read as
          an account already open, and nothing on a phone said how to get one. */}
      <button
        className="np-icon-btn np-narrow-only"
        onClick={() => setMobileSearch((v) => !v)}
        aria-label="Search"
        aria-expanded={mobileSearch}
      >
        <IconSearch className="np-icn" />
      </button>
      {showAccount ? (
        <Link className="np-icon-btn np-narrow-only" href="/account" aria-label="Your account">
          <IconAccount className="np-icn" />
        </Link>
      ) : (
        <Link className="np-signup-pill np-narrow-only" href={signupHref}>
          Sign up
        </Link>
      )}
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
            <div className="np-sheet-odds">
              <span>Odds</span>
              <OddsToggle />
            </div>
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
                <Link className="np-sheet-item is-primary" href={signupHref} onClick={() => setSheet(false)}>
                  Sign up — free
                </Link>
                <Link className="np-sheet-item" href="/pricing" onClick={() => setSheet(false)}>
                  Pricing
                </Link>
                <div className="np-sheet-note">
                  Every odds page works without an account.
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
      {/* The scrolling tape of board counts is gone: "clean books", "clean
          share" and "liquidity" in a green-and-red marquee read as a trading
          terminal, and it counted soccer alone on a site with seven sports. */}
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
            <OddsToggle className="np-odds-nav" />
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
          Odds comparison for Polymarket and Kalshi. Every number here is measured, and nothing
          here is a tip.
        </div>
        {/* What a US reader expects at the foot of anything about betting: the
            age line, what this is and is not, and where to get help. "Paper
            only, no real money" described the agent, not the site. */}
        <div className="np-footer-legal">
          18+ · A RESEARCH TOOL, NOT A TIP SERVICE · WE DO NOT PLACE TRADES OR HOLD FUNDS · NOT
          FINANCIAL OR BETTING ADVICE · PAST RESULTS DO NOT PREDICT FUTURE RESULTS
        </div>
        <div className="np-footer-legal">GAMBLING PROBLEM? CALL 1-800-GAMBLER</div>
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
