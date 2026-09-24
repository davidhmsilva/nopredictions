import type { Metadata } from 'next'

/** The page itself is a client component, which cannot export metadata — the
 *  same reason /wallet has one of these. Worth having here rather than
 *  inheriting the root title: this is a page people search for by name. */
export const metadata: Metadata = {
  title: 'Dropping odds: biggest line moves today — NOPREDICTIONS',
  description:
    'Every game on Polymarket — soccer, NFL, college football, MLB, NBA, NHL and WNBA — ranked by the side that shortened most in the last 24 hours. Pre-match only, on books with real money through them.',
  alternates: { canonical: '/dropping-odds' },
}

export default function DroppingOddsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
