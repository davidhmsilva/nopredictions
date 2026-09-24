import Link from 'next/link'
import { AppShell } from './AppShell'

/** The frame for the three legal pages: Terms, Privacy, Refunds.
 *
 *  Who runs the service, how to reach us and when a page last changed live
 *  here once, so the three can never name a different company or address. */

export const OPERATOR = 'NOPREDICTIONS'
export const CONTACT_EMAIL = 'nopredictions.info@gmail.com'
export const LEGAL_UPDATED = '24 September 2026'

const PAGES = [
  { href: '/terms', label: 'Terms of Service' },
  { href: '/privacy', label: 'Privacy Policy' },
  { href: '/refunds', label: 'Refund Policy' },
]

export function Mail() {
  return <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
}

export function LegalPage({
  path,
  title,
  lede,
  children,
}: {
  path: string
  title: string
  lede: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <AppShell>
      <article className="legal">
        <nav className="legal-nav" aria-label="Legal">
          {PAGES.map((p) => (
            <Link key={p.href} href={p.href} className={p.href === path ? 'is-on' : ''}>
              {p.label}
            </Link>
          ))}
        </nav>
        <h1>{title}</h1>
        <p className="legal-updated">Last updated {LEGAL_UPDATED}</p>
        <p className="legal-lede">{lede}</p>
        {children}
        <p className="legal-contact">
          Questions about this page: <Mail />.
        </p>
      </article>
    </AppShell>
  )
}
