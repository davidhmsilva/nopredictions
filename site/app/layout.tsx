import type { Metadata, Viewport } from 'next'
import './globals.css'

// What the site leads with, in one place, so the browser tab, a search result
// and a link pasted on X all say the same thing. The title names what the tool
// DOES, not a venue or a sport; the description says what is on it today.
const TITLE = 'NOPREDICTIONS — See if the price is wrong before you trade it'
const DESCRIPTION =
  'Kalshi and Polymarket side by side for the NFL, college football, MLB, NBA, NHL and WNBA, and every Polymarket soccer board checked against how matches priced the same way actually ended. American, decimal or implied odds. No tips: the numbers, and you decide.'
const CARD =
  'Kalshi and Polymarket side by side for US sports, and every Polymarket soccer board checked against the sharp closing line. No tips.'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: '/' },
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
    url: 'https://nopredictions.com',
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
      <body>{children}</body>
    </html>
  )
}
