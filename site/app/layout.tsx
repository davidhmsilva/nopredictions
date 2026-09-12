import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — Research Polymarket football before you bet',
  description:
    'Every football game on Polymarket today, in decimal odds. Open one to see how matches priced the same way actually ended, both teams’ form against their closing odds, the line-ups and a plain-English brief. Test your own betting theory on 111,475 real games.',
  alternates: { canonical: '/' },
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: 'NOPREDICTIONS — Research Polymarket football before you bet',
    description:
      'Every football game on Polymarket today, with the numbers behind the price: how matches priced the same way ended, form against the closing odds, line-ups and a brief.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — Research Polymarket football before you bet',
    description:
      'Every football game on Polymarket today, with the numbers behind the price: how matches priced the same way ended, form against the closing odds, line-ups and a brief.',
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
