import type { Metadata } from 'next'
import './globals.css'

// What the site leads with, in one place, so the browser tab, a search result
// and a link pasted on X all say the same thing. It names what the tool DOES,
// not a venue or a sport: the board is Polymarket football today, and the day
// that changes, only the second sentence of the description has to.
const TITLE = 'NOPREDICTIONS — See if the price is wrong before you trade it'
const DESCRIPTION =
  'Prediction-market prices checked against how matches priced the same way actually ended, the sharp closing line, and the book you would be trading into. Polymarket football today, in American or decimal odds. No tips: the numbers, and you decide.'
const CARD =
  'Prediction-market prices checked against how the same prices actually ended, the sharp closing line and the book you would trade into. No tips.'

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
