import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — Where a price actually exists',
  description:
    'Every football board Polymarket has open, graded on book quality. Test your own theory against 111,475 real games. No tips — measured numbers, and the ones that are not measured say so.',
  alternates: { canonical: '/' },
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: 'NOPREDICTIONS — Where a price actually exists',
    description:
      'Today’s Polymarket football boards, graded on the one thing we measured to matter: whether there is a real two-sided book behind the quote.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — Where a price actually exists',
    description:
      'Today’s Polymarket football boards, graded on the one thing we measured to matter: whether there is a real two-sided book behind the quote.',
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
