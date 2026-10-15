import type { Metadata, Viewport } from 'next'
import './globals.css'
import { Analytics } from '@vercel/analytics/next'

// What the site leads with, in one place, so the browser tab, a search result
// and a link pasted on X all say the same thing. Since 2026-10-15 it leads with
// the agent, like the hero; the description still names both venues, because
// those are the words people search, and says the agent paper-trades.
const TITLE = 'NOPREDICTIONS — Build an AI trading agent for prediction markets'
const DESCRIPTION =
  'Describe a strategy in plain English. Your AI agent backtests it on a decade of real games, then paper-trades it on Polymarket and Kalshi. Free odds on every game.'
const CARD =
  'Your own AI agent, hunting edges in prediction markets. Describe a strategy, see if it would have made money, and let it trade the games that fit.'

export const metadata: Metadata = {
  metadataBase: new URL('https://www.nopredictions.com'),
  title: TITLE,
  description: DESCRIPTION,
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: TITLE,
    description: CARD,
    type: 'website',
    url: 'https://www.nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: TITLE,
    description: CARD,
    images: ['/banner.jpg'],
  },
}

// The page's own background, so a phone browser tints its toolbar to match
// instead of framing a dark site in white. Width and scale stay Next's
// defaults (device-width, 1) — zoom is never locked.
export const viewport: Viewport = {
  themeColor: '#0b0d10',
  colorScheme: 'dark',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  )
}
