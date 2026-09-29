import type { Metadata } from 'next'
import { LAB_FOOTBALL_MATCHES, LAB_NBA_GAMES, LAB_TOTAL_GAMES, labCount } from '../lib/labData'

// The page is a client component and cannot export metadata itself.
export const metadata: Metadata = {
  title: `Lab: test a betting strategy on ${labCount(LAB_TOTAL_GAMES)} real games — NOPREDICTIONS`,
  description:
    `Write a betting theory in plain English and see how it would have done against the closing price, over ${labCount(LAB_FOOTBALL_MATCHES)} real soccer games and ${labCount(LAB_NBA_GAMES)} NBA games. Free to try.`,
  alternates: { canonical: '/lab' },
}

export default function LabLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
