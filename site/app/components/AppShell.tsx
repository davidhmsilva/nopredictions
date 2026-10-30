'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { IconAccount, IconAgent, IconCreate, IconMarket, IconMenu } from './icons'
import { invalidateSession, useSession } from '../lib/useSession'
import { OddsToggle } from './OddsToggle'

/** The tabs, in the order of the one job this app does (2026-10-28): turn an
 *  idea into an agent, look after your agents, or copy someone else's. The
 *  odds boards, the Game Center, team pages, results and the Wallet left the
 *  product that day — the user's call — and their URLs redirect here
 *  (next.config.mjs). Home is the brand mark, not a tab. */
export const TABS: {
  href: string
  label: string
  hint: string
  Icon: (p: { className?: string }) => JSX.Element
}[] = [
  { href: '/create',      label: 'Create',      hint: 'Idea to agent',  Icon: IconCreate },
  { href: '/agent',       label: 'My agents',   hint: 'Run and manage', Icon: IconAgent },
  { href: '/marketplace', label: 'Marketplace', hint: 'Copy an agent',  Icon: IconMarket },
]

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + '/')
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
    //    the shared bundle — measured at +70kB of First Load JS per page, paid
    //    by every visitor to carry a button most of them never press. Signing
    //    out can afford a round trip; opening a page cannot.
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
          <Link href="/pricing" className="np-btn-ghost np-wide-only np-nav-pricing">Pricing</Link>
          <Link href={loginHref} className="np-btn-ghost np-wide-only">Log in</Link>
          <Link href={signupHref} className="np-btn-signup np-wide-only">Sign up</Link>
        </>
      )}

      {/* Phones: who you are, then the menu. Signed out, "who you are" is a
          Sign up button, not a person icon: the icon read as an account
          already open, and nothing on a phone said how to get one. */}
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
                  Browsing the Marketplace needs no account. Building, running and
                  copying agents does — free, no card.
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
      <header className="np-nav">
        <div className="np-nav-inner">
          <Link href="/" className="np-brand">
            <span className="np-brand-mark" aria-hidden="true" />
            <span className="np-brand-text">
              <span className="np-brand-name">NOPREDICTIONS</span>
              <span className="np-brand-sub">Your idea, as an AI agent.</span>
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
              </Link>
            ))}
          </nav>

          <div className="np-nav-right">
            {/* Agents' bets and backtests print odds, so the format choice
                stays. The team search went with the team pages. */}
            <OddsToggle className="np-odds-nav" />
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
          AI agents for prediction markets. Describe an idea and get an agent that trades it, run
          your own, or copy one that is already working. Every number is measured, and nothing
          here is a tip.
        </div>
        {/* What a US reader expects at the foot of anything about betting: the
            age line, what this is and is not, and where to get help. Since
            the whole site is agents, "they paper-trade" is the site's line. */}
        <div className="np-footer-legal">
          18+ · AGENTS PAPER-TRADE: PRACTICE BETS AT REAL PRICES, NO REAL MONEY · NOT A TIP SERVICE ·
          WE DO NOT PLACE TRADES OR HOLD FUNDS · NOT FINANCIAL OR BETTING ADVICE · PAST RESULTS DO
          NOT PREDICT FUTURE RESULTS
        </div>
        <div className="np-footer-legal">GAMBLING PROBLEM? CALL 1-800-GAMBLER</div>
        <nav className="np-footer-links" aria-label="Legal">
          <Link href="/terms">Terms</Link>
          <Link href="/privacy">Privacy</Link>
          <Link href="/refunds">Refunds</Link>
          <a href="mailto:nopredictions.info@gmail.com">Contact</a>
        </nav>
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
