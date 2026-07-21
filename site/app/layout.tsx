import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://nopredictions.com'),
  title: 'NOPREDICTIONS — Test Any Betting Idea With AI',
  description:
    'Write a betting idea in normal English. Our AI tests it against 139,000 real matches and today’s odds — then places the bets for you if it makes money.',
  alternates: { canonical: '/' },
  icons: {
    icon: [
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: '/apple-touch-icon.png',
  },
  openGraph: {
    title: 'NOPREDICTIONS — Test Any Betting Idea With AI',
    description:
      'Is your betting idea profitable? Say it in normal English, our AI checks it against 139,000 real matches and today’s odds — then bets it for you.',
    type: 'website',
    url: 'https://nopredictions.com',
    siteName: 'NOPREDICTIONS',
    images: [{ url: '/banner.jpg', width: 1200, height: 460 }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'NOPREDICTIONS — Test Any Betting Idea With AI',
    description:
      'Is your betting idea profitable? Say it in normal English, our AI checks it against 139,000 real matches and today’s odds — then bets it for you.',
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
