import type { Metadata } from 'next'

// The page is a client component and cannot export metadata itself.
export const metadata: Metadata = {
  title: 'Pricing — NOPREDICTIONS',
  description:
    'Odds comparison is free, forever. Pro removes the daily limits on the Lab and the Wallet. 14-day money-back guarantee.',
  alternates: { canonical: '/pricing' },
}

export default function PricingLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
