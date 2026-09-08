import type { Metadata } from 'next'

/** The page itself is a client component, which cannot export metadata — the
 *  same reason /wallet has one of these. Worth having here rather than
 *  inheriting the root title: this is a page people search for by name. */
export const metadata: Metadata = {
  title: 'Dropping odds — NOPREDICTIONS',
  description:
    'Polymarket football, ranked by the side that shortened most in the last 24 hours. Pre-match only, and only on books with real money through them.',
}

export default function DroppingOddsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
