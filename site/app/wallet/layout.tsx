import type { Metadata } from 'next'

// The analyser is deliberately unlinked from the site's navigation, so it must
// also stay out of the index — a page that reads other people's wallets should
// be something you are given, not something you find.
export const metadata: Metadata = {
  title: 'Wallet analyser — NOPREDICTIONS',
  description: "Rebuild a Polymarket wallet's entire trading record into FIFO round trips.",
  robots: { index: false, follow: false },
}

export default function WalletLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
