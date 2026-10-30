import type { Metadata } from 'next'

// The pages are client components and cannot export metadata themselves. A
// user's agents are private, so nothing under here is worth indexing.
export const metadata: Metadata = {
  title: 'My agents — NOPREDICTIONS',
  description: 'Run, pause, publish and follow the record of your AI betting agents.',
  robots: { index: false, follow: false },
}

export default function AgentLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
