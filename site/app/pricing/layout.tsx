import type { Metadata } from 'next'

// The page is a client component and cannot export metadata itself.
export const metadata: Metadata = {
  title: 'Pricing — NOPREDICTIONS',
  description:
    'Build AI betting agents free: three a day from your own words, one running at a time. Pro removes the daily limit and runs ten at once. 14-day money-back guarantee.',
  alternates: { canonical: '/pricing' },
}

export default function PricingLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
