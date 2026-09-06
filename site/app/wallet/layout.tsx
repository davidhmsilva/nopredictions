import type { Metadata } from 'next'

// ⚠️ This said "deliberately unlinked from the site's navigation", which stopped
// being true on 2026-09-06 when Wallet became a tab. The noindex is KEPT anyway
// and is now a separate decision from the one that produced it: the page reads
// other people's wallets, and a tab being public to visitors is not the same as
// those records being surfaced in search results. Revisit deliberately.
export const metadata: Metadata = {
  title: 'Wallet analyser — NOPREDICTIONS',
  description: "Rebuild a Polymarket wallet's entire trading record into FIFO round trips.",
  robots: { index: false, follow: false },
}

export default function WalletLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
