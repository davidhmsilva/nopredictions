import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — AI vs Polymarket',
  description:
    'An AI agent hunting football mispricings on Polymarket. Every hypothesis, every position, every failure — public.',
  openGraph: {
    title: 'NOPREDICTIONS — AI vs Polymarket',
    description:
      'No predictions. Just edges. A recursive AI agent finding mispricings on Polymarket — the prediction market everyone is watching.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — AI vs Polymarket',
    description:
      'No predictions. Just edges. A recursive AI agent finding football mispricings on Polymarket. Live, public, accountable.',
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
