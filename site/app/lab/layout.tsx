import type { Metadata } from 'next'

// The page is a client component and cannot export metadata itself.
export const metadata: Metadata = {
  title: 'Lab: test a betting strategy on 111,475 real games — NOPREDICTIONS',
  description:
    'Write a betting theory in plain English and see how it would have done against the closing price, over 111,475 real soccer games and 10,006 NBA games. Free to try.',
  alternates: { canonical: '/lab' },
}

export default function LabLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
