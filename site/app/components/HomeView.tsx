'use client'

/** Home (2026-10-28): the app does one thing — an idea in, an agent out — so
 *  the page opens on the box you type the idea into. Under it, how the idea
 *  becomes an agent, and the agents already on the Marketplace for anyone who
 *  would rather copy than build.
 *
 *  The odds board that used to be the home page left the product the same
 *  day; nothing here is a list of games.
 */

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState, type FormEvent } from 'react'
import type { AgentSummary } from '../lib/agents'
import { relationOf, useMarketplaceMe } from '../lib/useMarketplaceMe'
import { MarketCard } from '../marketplace/MarketCard'

const EXAMPLES = [
  'Back home favourites in La Liga when they are in poor form',
  'Draws in Serie B when both teams are in poor form',
  'Back a 1.30-1.50 favourite that is pressing while level, sell after the next goal',
]

/** How many Marketplace agents the home page shows. */
const HOME_AGENTS = 6

/** How an idea becomes an agent — the four things Create actually does. */
const STEPS = [
  { title: 'Describe your idea', body: 'In your own words, the way you would tell a friend.' },
  { title: 'The AI writes the strategy', body: 'It asks what your idea leaves open, then sets exact rules: what to back, when, where, at what price.' },
  { title: 'See what it would have made', body: 'Replayed over 14 years of real odds, with how sure the result is.' },
  { title: 'Deploy your agent', body: 'It bets every game that fits, at Polymarket’s real price, and you watch its record build.' },
]

/** `initial` is null when the server got no answer in time; the browser then
 *  asks, and until it knows the page does not claim the Marketplace is empty. */
export function HomeView({ initial }: { initial: AgentSummary[] | null }) {
  const router = useRouter()
  const me = useMarketplaceMe()
  const [idea, setIdea] = useState('')
  const [listed, setListed] = useState<AgentSummary[] | null>(initial)

  useEffect(() => {
    if (initial) return
    fetch('/api/marketplace')
      .then((r) => r.json())
      .then((d) => d.ok && setListed(d.agents))
      .catch(() => {})
  }, [initial])

  const agents = listed?.slice(0, HOME_AGENTS) ?? null

  function build(e?: FormEvent) {
    e?.preventDefault()
    const v = idea.trim()
    router.push(v ? `/create?idea=${encodeURIComponent(v.slice(0, 500))}` : '/create')
  }

  return (
    <div className="np-wrap hm-page">
      <section className="hm-hero">
        <p className="sc-eyebrow">NOPREDICTIONS · AI agents for prediction markets</p>
        <h1 className="hm-h1">Turn your betting idea into an AI agent</h1>
        <p className="hm-sub">
          Describe it in your own words. The AI turns it into a strategy with exact rules, shows what
          it would have made over 14 years of real odds, and deploys it as an agent that trades every
          game that fits.
        </p>

        <form className="cr-idea hm-idea" onSubmit={build}>
          <label htmlFor="hm-idea-box" className="cr-idea-label">
            What&apos;s your idea?
          </label>
          <textarea
            id="hm-idea-box"
            className="cr-idea-box"
            placeholder="e.g. Back home favourites in La Liga when they are in poor form"
            value={idea}
            maxLength={500}
            rows={3}
            onChange={(e) => setIdea(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                build()
              }
            }}
          />
          <div className="cr-idea-foot">
            <span className="hm-idea-note">Free: three agents a day from your own words. No card.</span>
            <button type="submit" className="tp-go cr-go">
              Build my agent <span aria-hidden="true">→</span>
            </button>
          </div>
        </form>
        <div className="hm-examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="tp-chip" onClick={() => setIdea(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </section>

      <section className="hm-how" aria-labelledby="hm-how-h">
        <h2 id="hm-how-h" className="hm-h2">From an idea to an agent</h2>
        <ol className="sc-steps hm-flow">
          {STEPS.map((s, i) => (
            <li key={s.title} className="sc-step">
              <span className="sc-step-n" aria-hidden="true">{i + 1}</span>
              <b className="sc-step-t">{s.title}</b>
              <span className="sc-step-b">{s.body}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="hm-market" aria-labelledby="hm-market-h">
        <div className="hm-market-head">
          <div>
            <h2 id="hm-market-h" className="hm-h2">Or copy an agent that is already running</h2>
            <p className="hm-market-sub">
              Built by other people from their own ideas, published with the strategy and the whole
              record. Copy one and it runs in your account.
            </p>
          </div>
          <Link href="/marketplace" className="np-btn">
            Browse the Marketplace <span aria-hidden="true">→</span>
          </Link>
        </div>
        {agents === null ? null : agents.length > 0 ? (
          <div className="mk-grid">
            {agents.map((a) => (
              <MarketCard key={a.id} a={a} relation={relationOf(me, a.id)} />
            ))}
          </div>
        ) : (
          <div className="ag-empty">
            <b>The Marketplace is just opening.</b> Build an agent from your idea, let it run, and
            publish it — the first ones published are the first ones copied.
          </div>
        )}
      </section>

      <section className="hm-manage">
        <div>
          <h2 className="hm-h2">All your agents in one place</h2>
          <p>
            Run them, pause them, rename them, and see every bet each one placed, its profit curve and
            whether the price moved its way. Publish the ones worth copying.
          </p>
        </div>
        <Link href="/agent" className="np-btn np-btn-primary">
          My agents <span aria-hidden="true">→</span>
        </Link>
      </section>

      <p className="np-note hm-honest">
        <strong>Paper trading, for now.</strong> Agents place practice bets of 1 unit at real
        Polymarket prices and settle them on the real result, so every record is real and no money
        is at stake. Nothing here is a tip, and a backtest is not a promise.
      </p>
    </div>
  )
}
