import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — Today’s football, priced',
  description:
    'Every football market Polymarket has open today, biggest first, in decimal odds. Test your own betting theory against 111,475 real games, and read any trader’s whole record.',
  alternates: { canonical: '/' },
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: 'NOPREDICTIONS — Today’s football, priced',
    description:
      'Every football market Polymarket has open today, biggest first, in decimal odds — plus a lab for your own betting theories and a reader for any trader’s record.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — Today’s football, priced',
    description:
      'Every football market Polymarket has open today, biggest first, in decimal odds — plus a lab for your own betting theories and a reader for any trader’s record.',
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
