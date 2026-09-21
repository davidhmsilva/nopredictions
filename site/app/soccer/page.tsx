import type { Metadata } from 'next'
import { SoccerBoard } from '../components/SoccerBoard'

// Every soccer game on the board — what the home page lists the top ten of.
// A static route, so it wins over the [sport] segment the US sports use.

const title = 'Soccer odds: Polymarket vs Kalshi — NOPREDICTIONS'
const description =
  'Every soccer game on Polymarket and Kalshi, side by side, with the better price marked after fees. Biggest games first, and a full match page for each. Free.'

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: '/soccer' },
  openGraph: {
    title,
    description,
    url: 'https://nopredictions.com/soccer',
    type: 'website',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: { card: 'summary_large_image', title, description, images: ['/banner.jpg'] },
}

export default function SoccerPage() {
  return <SoccerBoard />
}
