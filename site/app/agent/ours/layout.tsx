import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Our agent — NOPREDICTIONS',
  description:
    "The only agent running: its paper record, every strategy, and the arms that lost. Nothing here is live money.",
}

export default function OurAgentLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
