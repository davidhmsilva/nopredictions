import type { Metadata } from 'next'

// The page is a client component and cannot export metadata itself.
export const metadata: Metadata = {
  title: 'Create an AI betting agent from your idea — NOPREDICTIONS',
  description:
    'Describe a betting idea in your own words. The AI turns it into a strategy with exact rules, tests it on up to 14 years of real odds, and deploys it as an agent that paper-trades every game that fits.',
  alternates: { canonical: '/create' },
}

export default function CreateLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
