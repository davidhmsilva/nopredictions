'use client'

import { useState, useEffect } from 'react'
import {
  fetchDbStats,
  fetchLeaderboard,
  fetchPaperTrades,
  type DbStats,
  type Strategy,
  type PaperTrade,
} from './lib/supabase'
import { VALID_SECTIONS, type Section } from './lib/types'
import { Ticker } from './components/Ticker'
import { Nav, MobileNav } from './components/Nav'
import { HomeSection } from './components/HomeSection'
import { AgentSection } from './components/AgentSection'
import { NewsletterSection } from './components/NewsletterSection'
import { AboutSection } from './components/AboutSection'
import { Footer } from './components/Footer'

// -- Hash routing -----------------------------------------------------------

function readSectionFromHash(): Section {
  if (typeof window === 'undefined') return 'home'
  const raw = window.location.hash.replace(/^#/, '')
  // Backwards compat: old #strategies and #leaderboard → #agent
  if (raw === 'strategies' || raw === 'leaderboard') return 'agent'
  const h = raw as Section
  return VALID_SECTIONS.includes(h) ? h : 'home'
}

// -- Root page --------------------------------------------------------------

export default function Page() {
  const [section, setSectionState] = useState<Section>('home')
  const [stats, setStats] = useState<DbStats | null>(null)
  const [trades, setTrades] = useState<PaperTrade[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loadingStats, setLoadingStats] = useState(true)
  const [loadingTrades, setLoadingTrades] = useState(true)
  const [loadingStrategies, setLoadingStrategies] = useState(true)

  // Sync section from URL hash on mount + listen for back/forward
  useEffect(() => {
    setSectionState(readSectionFromHash())
    const onPop = () => setSectionState(readSectionFromHash())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // setSection wrapper: pushes to history so back button works
  const setSection = (s: Section) => {
    if (s === section) return
    if (typeof window !== 'undefined') {
      const newUrl = s === 'home' ? window.location.pathname : `#${s}`
      window.history.pushState({ section: s }, '', newUrl)
      window.scrollTo(0, 0)
    }
    setSectionState(s)
  }

  // Fetch all data on mount
  useEffect(() => {
    fetchDbStats()
      .then(setStats)
      .finally(() => setLoadingStats(false))

    fetchPaperTrades()
      .then(setTrades)
      .finally(() => setLoadingTrades(false))

    fetchLeaderboard()
      .then(setStrategies)
      .finally(() => setLoadingStrategies(false))
  }, [])

  return (
    <>
      <Ticker />
      <Nav section={section} setSection={setSection} />

      {section === 'home' && (
        <HomeSection
          stats={stats}
          trades={trades}
          strategies={strategies}
          setSection={setSection}
          loading={loadingStats || loadingTrades || loadingStrategies}
        />
      )}
      {section === 'agent' && (
        <AgentSection
          trades={trades}
          strategies={strategies}
          loading={loadingTrades || loadingStrategies}
        />
      )}
      {section === 'newsletter' && <NewsletterSection />}
      {section === 'about' && <AboutSection stats={stats} loading={loadingStats} />}

      <Footer setSection={setSection} />
      <MobileNav section={section} setSection={setSection} />
    </>
  )
}
