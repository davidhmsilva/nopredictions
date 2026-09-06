'use client'

import { useState, useEffect } from 'react'
import {
  fetchDbStats,
  fetchLeaderboard,
  fetchPaperTrades,
  type DbStats,
  type Strategy,
  type PaperTrade,
} from '../lib/supabase'
import { VALID_SECTIONS, type Section } from '../lib/types'
import { AppShell } from '../components/AppShell'
import { Ticker } from '../components/Ticker'
import { HomeSection } from '../components/HomeSection'
import { AgentSection } from '../components/AgentSection'

// -- Hash routing -----------------------------------------------------------

function readSectionFromHash(): Section {
  if (typeof window === 'undefined') return 'overview'
  const raw = window.location.hash.replace(/^#/, '')
  // Every URL the old dashboard ever published still lands somewhere sensible.
  if (raw === 'strategies' || raw === 'leaderboard' || raw === 'agent') return 'strategies'
  const h = raw as Section
  return VALID_SECTIONS.includes(h) ? h : 'overview'
}

// -- The "not live" banner ---------------------------------------------------
//
// Every arm on this page is paper. The last real-money trade was 2026-08-09 and
// none of the three running strategies is near its verdict gate (n>=200 settled
// with a confidence interval clear of zero after the fee). Saying so at the top
// is not modesty -- a visitor who reads a yield here and assumes it is a live,
// validated return has been misled by the page rather than by the number.

function TestingBanner() {
  return (
    <div className="ag-banner">
      <div className="np-wrap ag-banner-inner">
        <span className="np-badge is-warn">IN TESTING</span>
        <p className="ag-banner-text">
          The trading agent is <strong>not live</strong>. Everything below is paper:
          simulated stakes, real prices, logged before each event resolved. No strategy
          here has reached its verdict gate yet, so treat the yields as a record of what
          was tried, not as a return you could have earned. The full record is public
          anyway — including the losing arms.
        </p>
      </div>
    </div>
  )
}

// -- Page --------------------------------------------------------------------

export default function AgentPage() {
  const [section, setSectionState] = useState<Section>('overview')
  const [stats, setStats] = useState<DbStats | null>(null)
  const [trades, setTrades] = useState<PaperTrade[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loadingStats, setLoadingStats] = useState(true)
  const [loadingTrades, setLoadingTrades] = useState(true)
  const [loadingStrategies, setLoadingStrategies] = useState(true)

  useEffect(() => {
    setSectionState(readSectionFromHash())
    const onPop = () => setSectionState(readSectionFromHash())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const setSection = (s: Section) => {
    if (s === section) return
    if (typeof window !== 'undefined') {
      const newUrl = s === 'overview' ? window.location.pathname : `#${s}`
      window.history.pushState({ section: s }, '', newUrl)
      window.scrollTo(0, 0)
    }
    setSectionState(s)
  }

  useEffect(() => {
    fetchDbStats().then(setStats).finally(() => setLoadingStats(false))
    fetchPaperTrades().then(setTrades).finally(() => setLoadingTrades(false))
    fetchLeaderboard().then(setStrategies).finally(() => setLoadingStrategies(false))
  }, [])

  return (
    <AppShell>
      <TestingBanner />
      <Ticker trades={trades} />

      <div className="np-wrap ag-subtabs-wrap">
        <div className="ag-subtabs" role="tablist">
          <button
            role="tab"
            aria-selected={section === 'overview'}
            className={`ag-subtab${section === 'overview' ? ' is-on' : ''}`}
            onClick={() => setSection('overview')}
          >
            Overview
          </button>
          <button
            role="tab"
            aria-selected={section === 'strategies'}
            className={`ag-subtab${section === 'strategies' ? ' is-on' : ''}`}
            onClick={() => setSection('strategies')}
          >
            Strategies &amp; trades
          </button>
        </div>
      </div>

      {section === 'overview' && (
        <HomeSection
          stats={stats}
          trades={trades}
          strategies={strategies}
          setSection={setSection}
          loading={loadingStats || loadingTrades || loadingStrategies}
        />
      )}
      {section === 'strategies' && (
        <AgentSection
          trades={trades}
          strategies={strategies}
          loading={loadingTrades || loadingStrategies}
        />
      )}
    </AppShell>
  )
}
