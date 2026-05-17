import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — AI vs Prediction Markets',
  description:
    'An AI agent hunting football mispricings on Polymarket. Every hypothesis, every position, every failure — public.',
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: 'NOPREDICTIONS — AI vs Prediction Markets',
    description:
      'No predictions. Just edges. An AI agent finding mispricings on prediction markets — the markets everyone is watching.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — AI vs Prediction Markets',
    description:
      'No predictions. Just edges. An AI agent finding football mispricings on prediction markets. Live, public, accountable.',
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
