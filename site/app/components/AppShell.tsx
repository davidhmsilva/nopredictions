'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

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
            <span className="np-paper-pill" title="No real money is at risk anywhere on this site.">
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
