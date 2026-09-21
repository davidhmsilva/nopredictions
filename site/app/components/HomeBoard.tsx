'use client'

/** The home page: the biggest games in every sport, then a way into each.
 *
 *    what the site does, in one line
 *    the biggest games right now        ← every sport on one grid, by money traded
 *    browse by sport                    ← each sport's own page
 *    how it works                       ← three steps, no arithmetic
 *    what else there is                 ← Lab, Agents, Wallet
 *
 *  It used to be the top ten of the soccer board. The site covers seven
 *  sports, so a front page of soccer alone told most visitors it was not for
 *  them.
 *
 *  The grid waits until the boards have answered (or a few seconds, whichever
 *  is first) before it ranks anything: ranking on whichever sport answered
 *  first and then reshuffling as the rest land reads as a broken page.
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AppShell } from './AppShell'
import { SportBar } from './SportBar'
import { GameCard, GameCardSkeleton } from './GameCard'
import { VenueLogo } from './VenueLogo'
import { money } from './boardParts'
import { IconAgent, IconLab, IconWallet } from './icons'
import { useAllSports, useSoccerRows } from './useBoards'
import { hasPrice, rankRows, type BoardRow, type BoardSport } from '../lib/boardRow'
import { SPORT_KEYS, SPORT_META } from '../lib/sportsMeta'
import { useSession } from '../lib/useSession'

/** Two rows of four on a desktop. */
const TOP_CARDS = 8
/** How long the grid waits for a slow board before ranking what it has. */
const WAIT_MS = 5000

const SPORT_TILE: Record<BoardSport, { label: string; path: string }> = {
  soccer: { label: 'Soccer', path: '/soccer' },
  ...(Object.fromEntries(
    SPORT_KEYS.map((k) => [k, { label: SPORT_META[k].label, path: SPORT_META[k].path }])
  ) as Record<Exclude<BoardSport, 'soccer'>, { label: string; path: string }>),
}

const TOOLS = [
  {
    href: '/lab',
    Icon: IconLab,
    title: 'Test your betting idea',
    body: 'Type a theory in plain English — “home underdogs in Serie A” — and see how it would have done over 111,475 real games.',
    cta: 'Try the Lab',
  },
  {
    href: '/agent',
    Icon: IconAgent,
    title: 'Let an agent follow it',
    body: 'Save your idea as an agent and it paper-trades every new game that fits. Watch the record build — no money involved.',
    cta: 'See Agents',
  },
  {
    href: '/wallet',
    Icon: IconWallet,
    title: 'Look inside a top trader',
    body: 'Paste any Polymarket wallet and see every bet, what it won, and where the profit really came from.',
    cta: 'Open Wallet',
  },
]

const STEPS = [
  {
    title: 'Both apps, live',
    body: 'We read every price on Polymarket and Kalshi, for every game, as it moves.',
  },
  {
    title: 'Fees taken out',
    body: 'Each app charges a small fee per trade, and Kalshi’s is higher. We compare what you actually pay.',
  },
  {
    title: 'The better price, marked',
    body: 'The app that pays more is outlined, with its logo. A tie is a tie — we never stretch it.',
  },
]

export function HomeBoard() {
  const soccer = useSoccerRows()
  const sports = useAllSports()
  const { me } = useSession()
  const [waited, setWaited] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setWaited(true), WAIT_MS)
    return () => clearTimeout(t)
  }, [])

  const bySport = useMemo(() => {
    const m = { soccer: soccer.rows } as Record<BoardSport, BoardRow[]>
    for (const k of SPORT_KEYS) m[k] = sports[k].rows
    return m
  }, [soccer.rows, sports])

  const settled = !soccer.loading && SPORT_KEYS.every((k) => !sports[k].loading)
  const ready = settled || waited

  const top = useMemo(() => {
    const all = Object.values(bySport).flat().filter((r) => !r.finished && hasPrice(r))
    return rankRows(all).slice(0, TOP_CARDS)
  }, [bySport])

  const tiles = useMemo(() => {
    const keys: BoardSport[] = ['soccer', ...SPORT_KEYS]
    return keys
      .map((k) => {
        const rows = bySport[k].filter((r) => !r.finished)
        return {
          key: k,
          ...SPORT_TILE[k],
          games: rows.length,
          live: rows.filter((r) => r.live).length,
          volume: rows.reduce((s, r) => s + r.volume, 0),
          loading: k === 'soccer' ? soccer.loading : sports[k as Exclude<BoardSport, 'soccer'>].loading,
        }
      })
      .sort((a, b) => b.volume - a.volume)
  }, [bySport, soccer.loading, sports])

  return (
    <AppShell>
      <SportBar />
      <div className="np-wrap">
        <section className="hm-hero">
          <h1 className="hm-h1">Find the better odds on every game.</h1>
          <p className="hm-sub">
            <span className="hm-venue">
              <VenueLogo venue="polymarket" size={18} /> Polymarket
            </span>{' '}
            and{' '}
            <span className="hm-venue">
              <VenueLogo venue="kalshi" size={18} /> Kalshi
            </span>{' '}
            side by side — NFL, college football, MLB, NBA, NHL, WNBA and soccer. We show you
            which one pays more.
          </p>
          <ul className="hm-trust">
            <li>Free to use</li>
            <li>Fees included</li>
            <li>No tips, just prices</li>
            <li>We never hold your money</li>
          </ul>
        </section>

        <section className="hm-sec" aria-labelledby="hm-top-h">
          <div className="hm-sec-head">
            <h2 id="hm-top-h" className="hm-h2">
              Biggest games right now
            </h2>
            <span className="hm-sec-note">By money traded on both apps</span>
          </div>
          <div className="gm-grid gm-grid-4">
            {!ready || (top.length === 0 && !settled)
              ? Array.from({ length: TOP_CARDS }, (_, i) => <GameCardSkeleton key={i} />)
              : top.map((r) => <GameCard key={`${r.sport}:${r.key}`} r={r} />)}
          </div>
          {ready && top.length === 0 && settled && (
            <div className="np-empty">No games on the board right now. Check back soon.</div>
          )}
        </section>

        <section className="hm-sec" aria-labelledby="hm-sports-h">
          <div className="hm-sec-head">
            <h2 id="hm-sports-h" className="hm-h2">
              Browse by sport
            </h2>
          </div>
          <div className="hm-sports">
            {tiles.map((t) => (
              <Link
                key={t.key}
                href={t.path}
                className={`hm-sport${!t.loading && t.games === 0 ? ' is-empty' : ''}`}
              >
                <b>{t.label}</b>
                <span className="np-num">
                  {t.loading
                    ? 'Loading…'
                    : t.games === 0
                      ? 'No games this week'
                      : `${t.games} game${t.games === 1 ? '' : 's'}${t.live ? ` · ${t.live} live` : ''}`}
                </span>
                {!t.loading && t.volume > 0 && (
                  <em className="np-num">{money(t.volume)} traded</em>
                )}
              </Link>
            ))}
          </div>
        </section>

        <section className="hm-sec" aria-labelledby="hm-how-h">
          <div className="hm-sec-head">
            <h2 id="hm-how-h" className="hm-h2">
              How it works
            </h2>
          </div>
          <ol className="hm-steps">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <span className="hm-step-n np-num">{i + 1}</span>
                <b>{s.title}</b>
                <span>{s.body}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="hm-sec hm-more" aria-labelledby="hm-more-h">
          <div className="hm-sec-head">
            <h2 id="hm-more-h" className="hm-h2">
              Go further
            </h2>
          </div>
          <div className="hm-more-grid">
            {TOOLS.map((m) => (
              <Link key={m.href} href={m.href} className="hm-more-card">
                <m.Icon className="hm-more-icn" />
                <b>{m.title}</b>
                <span>{m.body}</span>
                <em>
                  {m.cta} <span aria-hidden="true">→</span>
                </em>
              </Link>
            ))}
          </div>
          {/* Only once the session is known to be empty — rendering it while
              it loads would flash an invitation at people already signed in. */}
          {me && !me.user && (
            <div className="hm-join">
              <div className="hm-join-text">
                <b>Free to start. No card needed.</b>
                <span>
                  A free account gets you 3 strategy tests and 3 wallet reads a day, up to 5
                  agents, and a watchlist.
                </span>
              </div>
              <Link href="/login?mode=signup&next=%2F" className="np-btn np-btn-primary">
                Create a free account <span aria-hidden="true">→</span>
              </Link>
            </div>
          )}
        </section>
      </div>
    </AppShell>
  )
}
