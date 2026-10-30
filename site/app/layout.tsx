import type { Metadata, Viewport } from 'next'
import './globals.css'
import { Analytics } from '@vercel/analytics/next'

// What the site leads with, in one place, so the browser tab, a search result
// and a link pasted on X all say the same thing. Since 2026-10-28 the site is
// agents only: describe an idea, get an agent that trades it, or copy one.
const TITLE = 'NOPREDICTIONS — Turn your betting idea into an AI agent'
const DESCRIPTION =
  'Describe a betting idea in your own words. The AI turns it into a strategy, tests it on 14 years of real odds, and deploys it as an agent that paper-trades on Polymarket. Or copy an agent from the Marketplace.'
const CARD =
  'Your idea, as an AI agent. Describe a strategy, see what it would have made, deploy it — or copy an agent someone already runs.'

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
