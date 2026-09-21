'use client'

/** The Game Center for a US game — NFL, college football, MLB, NBA, NHL, WNBA.
 *
 *    Overview  who wins on both apps, the sportsbook line, ESPN's model,
 *              recent form, injuries
 *    Markets   every other market Polymarket lists on the game
 *    Stats     team numbers side by side, and each side's leaders
 *
 *  Drawn with the soccer Game Center's pieces (gc-hero, gcx-tabs, gc-section)
 *  so the two read as one product. Away on the left, home on the right, the
 *  way a US schedule prints a game.
 */

import { useEffect, useState } from 'react'
import { AppShell } from './AppShell'
import { VenueLogo } from './VenueLogo'
import { money, odds, paysMorePct } from './boardParts'
import { kickoffText, priceText, useOddsFormat, type OddsFormat } from '../lib/display'
import { SPORT_META, type SportKey } from '../lib/sportsMeta'
import type { GameTeam, PmMarketGroup, RecentGame, SportGamePage } from '../lib/sportGameTypes'
import { VENUE_NAME, type Venue } from '../lib/venues'
import { useSession } from '../lib/useSession'
import { useWatchlist } from '../lib/useWatchlist'

const REFRESH_MS = 60_000

const TABS = [
  ['overview', 'Overview'],
  ['markets', 'Markets'],
  ['stats', 'Stats'],
] as const
type Tab = (typeof TABS)[number][0]

/** An American moneyline as a probability, so it follows the reader's odds
 *  format like every other price on the site. */
function fromAmerican(ml: number | null): number | null {
  if (ml == null || ml === 0) return null
  return ml < 0 ? -ml / (-ml + 100) : 100 / (ml + 100)
}

// ── hero ─────────────────────────────────────────────────────────────────────

function HeroTeam({ t, side }: { t: GameTeam; side: 'home' | 'away' }) {
  return (
    <span className={`gc-hero-team${side === 'home' ? ' gc-hero-team-away' : ''}`}>
      {side === 'away' && t.logo && <img className="gcx-logo" src={t.logo} alt="" loading="lazy" />}
      <span>
        {t.short || t.name}
        <span className="gcx-hero-sub">
          {t.record && <span className="gcx-hero-pos">{t.record}</span>}
          {t.splitRecord && <span className="sg-split">{t.splitRecord}</span>}
        </span>
      </span>
      {side === 'home' && t.logo && <img className="gcx-logo" src={t.logo} alt="" loading="lazy" />}
    </span>
  )
}

function Hero({ g }: { g: SportGamePage }) {
  const started = g.state !== 'pre'
  const meta = [g.venue, g.broadcasts.join(' · ') || null].filter(Boolean)
  const periods = Math.max(g.home.periods.length, g.away.periods.length)
  return (
    <header className={`gc-hero${g.state === 'in' ? ' is-live' : ''}`}>
      <div className="gc-hero-top">
        <span className="gc-hero-comp">{SPORT_META[g.sport].label}</span>
        {g.state === 'post' ? (
          <span className="np-badge">{g.detail || 'Final'}</span>
        ) : g.state === 'in' ? (
          <span className="np-badge is-live">● {g.detail || 'Live'}</span>
        ) : (
          <span className="gc-hero-ko np-num">{kickoffText(new Date(g.start))}</span>
        )}
      </div>

      <div className="gc-hero-teams">
        <HeroTeam t={g.away} side="away" />
        {started && g.home.score != null && g.away.score != null ? (
          <span className="gc-hero-score np-num">
            {g.away.score}
            <i>–</i>
            {g.home.score}
          </span>
        ) : (
          <span className="gc-hero-v">@</span>
        )}
        <HeroTeam t={g.home} side="home" />
      </div>

      {started && periods > 0 && (
        <div className="sg-lines gcx-scroll">
          <table>
            <thead>
              <tr>
                <th />
                {Array.from({ length: periods }, (_, i) => (
                  <th key={i}>{i + 1}</th>
                ))}
                <th>T</th>
              </tr>
            </thead>
            <tbody>
              {[g.away, g.home].map((t) => (
                <tr key={t.abbr || t.name}>
                  <td>{t.abbr || t.short}</td>
                  {Array.from({ length: periods }, (_, i) => (
                    <td key={i} className="np-num">
                      {t.periods[i] ?? ''}
                    </td>
                  ))}
                  <td className="np-num sg-lines-t">{t.score ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {meta.length > 0 && <div className="gcx-hero-meta">{meta.join('  ·  ')}</div>}
    </header>
  )
}

// ── overview ─────────────────────────────────────────────────────────────────

/** Who wins, on both apps: the better price big, both apps' prices under it. */
function Moneyline({ g, f }: { g: SportGamePage; f: OddsFormat }) {
  const row = g.board
  if (!row) {
    return (
      <section className="gc-section">
        <h2 className="gc-h2">Who wins</h2>
        <p className="gc-quiet">
          {g.state === 'post'
            ? 'This game is over, and its markets have closed.'
            : 'Neither Polymarket nor Kalshi is offering this game right now.'}
        </p>
      </section>
    )
  }
  const sides: { key: 'away' | 'home'; t: GameTeam }[] = [
    { key: 'away', t: g.away },
    { key: 'home', t: g.home },
  ]
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Who wins</h2>
      <div className="sg-ml">
        {sides.map(({ key, t }) => {
          const pick = row.best[key]
          const more = paysMorePct(pick)
          return (
            <div key={key} className={`sg-ml-side${pick?.venue ? ' is-best' : ''}`}>
              <span className="sg-ml-team">{t.short || t.name}</span>
              <span className="sg-ml-price np-num">
                {pick?.venue && <VenueLogo venue={pick.venue} size={18} title="" />}
                {odds(pick?.ask ?? null, f)}
              </span>
              <span className="sg-ml-venues">
                {row.venues.map((b) => (
                  <span key={b.venue} className="sg-ml-venue">
                    <VenueLogo venue={b.venue} size={13} />
                    <span className="np-num">{odds(b.quotes[key]?.ask ?? null, f)}</span>
                  </span>
                ))}
              </span>
              <span className="sg-ml-note">
                {pick?.venue && more != null
                  ? `Pays ${more.toFixed(1)}% more on ${VENUE_NAME[pick.venue]}, after fees`
                  : pick?.quoted === 1
                    ? 'Only one app offers this'
                    : pick?.quoted
                      ? 'Same price on both apps'
                      : 'Not offered right now'}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/** The sportsbook line and ESPN's own model, beside what the exchanges say. */
function Elsewhere({ g, f }: { g: SportGamePage; f: OddsFormat }) {
  const sb = g.sportsbook
  const pr = g.predictor
  if (!sb && !pr) return null
  const market = (key: 'home' | 'away') => g.board?.best[key]?.ask ?? null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Elsewhere</h2>
      <div className="sg-else">
        {sb && (
          <div className="sg-else-card">
            <span className="sg-else-k">{sb.provider}, via ESPN</span>
            <div className="sg-else-rows">
              <span>
                Moneyline{' '}
                <b className="np-num">
                  {g.away.abbr} {fromAmerican(sb.awayMoneyline) != null ? priceText(fromAmerican(sb.awayMoneyline) as number, f) : '—'}
                </b>{' '}
                ·{' '}
                <b className="np-num">
                  {g.home.abbr} {fromAmerican(sb.homeMoneyline) != null ? priceText(fromAmerican(sb.homeMoneyline) as number, f) : '—'}
                </b>
              </span>
              {sb.details && (
                <span>
                  Spread <b className="np-num">{sb.details}</b>
                </span>
              )}
              {sb.overUnder != null && (
                <span>
                  Total <b className="np-num">{sb.overUnder}</b>
                </span>
              )}
            </div>
          </div>
        )}
        {pr && (
          <div className="sg-else-card">
            <span className="sg-else-k">Win chance</span>
            <div className="sg-chance">
              {(['away', 'home'] as const).map((k) => {
                const t = g[k]
                const m = market(k)
                return (
                  <div key={k} className="sg-chance-row">
                    <span className="sg-chance-team">{t.abbr || t.short}</span>
                    <span className="sg-chance-bar">
                      <i style={{ width: `${Math.min(100, Math.max(0, pr[k]))}%` }} />
                    </span>
                    <span className="np-num sg-chance-v">ESPN {pr[k].toFixed(0)}%</span>
                    <span className="np-num sg-chance-v">
                      Price {m != null ? `${Math.round(m * 100)}%` : '—'}
                    </span>
                  </div>
                )
              })}
            </div>
            <span className="sg-else-foot">
              ESPN&apos;s matchup model against the exchanges&apos; better price, read as a chance.
            </span>
          </div>
        )}
      </div>
    </section>
  )
}

function FormList({ games }: { games: RecentGame[] }) {
  if (!games.length) return <span className="gc-quiet">No recent games.</span>
  return (
    <ul className="sg-form">
      {games.map((r, i) => (
        <li key={i}>
          <span className={`sg-res is-${r.result}`}>{r.result || '·'}</span>
          <span className="sg-form-opp">
            {r.atVs} {r.opponent}
          </span>
          <span className="np-num sg-form-score">{r.score}</span>
        </li>
      ))}
    </ul>
  )
}

function TwoColumns({
  title,
  g,
  render,
}: {
  title: string
  g: SportGamePage
  render: (side: 'away' | 'home') => React.ReactNode
}) {
  return (
    <section className="gc-section">
      <h2 className="gc-h2">{title}</h2>
      <div className="sg-two">
        {(['away', 'home'] as const).map((k) => (
          <div key={k} className="sg-two-col">
            <span className="sg-two-h">{g[k].short || g[k].name}</span>
            {render(k)}
          </div>
        ))}
      </div>
    </section>
  )
}

// ── markets ──────────────────────────────────────────────────────────────────

const SHOWN = 6

function MarketGroup({ group, f }: { group: PmMarketGroup; f: OddsFormat }) {
  const [open, setOpen] = useState(false)
  const list = open ? group.markets : group.markets.slice(0, SHOWN)
  return (
    <section className="gc-section">
      <h2 className="gc-h2">
        {group.name} <span className="sg-count np-num">{group.markets.length}</span>
      </h2>
      <div className="sg-mkts">
        {list.map((m, i) => (
          <div key={i} className="sg-mkt">
            <span className="sg-mkt-t">{m.title}</span>
            <span className="sg-mkt-outs">
              {m.outcomes.map((o, j) => (
                <span key={j} className="sg-out">
                  <em>{o.label}</em>
                  <b className="np-num">{odds(o.ask, f)}</b>
                </span>
              ))}
            </span>
            <span className="sg-mkt-vol np-num">{money(m.volume)}</span>
          </div>
        ))}
      </div>
      {group.markets.length > SHOWN && (
        <button className="gc-more" onClick={() => setOpen((v) => !v)}>
          {open ? 'Show fewer' : `Show all ${group.markets.length}`}
        </button>
      )}
    </section>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export function SportGameView({ sport, id }: { sport: SportKey; id: string }) {
  const [g, setG] = useState<SportGamePage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const f = useOddsFormat()
  const { me } = useSession()
  const { slugs, toggle } = useWatchlist(me?.plan === 'pro')
  const watched = slugs.includes(id)

  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.slice(1)
      if (TABS.some(([k]) => k === h)) setTab(h as Tab)
    }
    fromHash()
    window.addEventListener('hashchange', fromHash)
    return () => window.removeEventListener('hashchange', fromHash)
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch(`/api/sports/${sport}/${id}`)
        .then(async (r) => {
          const b = await r.json()
          if (!r.ok || !b.ok) throw new Error(b.error ?? `HTTP ${r.status}`)
          return b as SportGamePage
        })
        .then((b) => {
          if (cancelled) return
          setG(b)
          setError(null)
        })
        .catch((e) => {
          // A failed refresh keeps the page already on screen.
          if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the game')
        })
    load()
    const t = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [sport, id])

  useEffect(() => {
    if (g) document.title = `${g.away.short} @ ${g.home.short} odds — NOPREDICTIONS`
  }, [g])

  const pick = (k: Tab) => {
    setTab(k)
    history.replaceState(null, '', `#${k}`)
  }

  return (
    <AppShell>
      <div className="gc-main">
        {!g && !error && <div className="gc-loading">Loading the game…</div>}
        {!g && error && (
          <div className="np-note sc-error">
            <strong>We couldn&apos;t load this game.</strong> {error}
          </div>
        )}

        {g && (
          <>
            <Hero g={g} />

            <nav className="gcx-tabs" role="tablist" aria-label="Game sections">
              {TABS.map(([k, label]) => (
                <button
                  key={k}
                  role="tab"
                  aria-selected={tab === k}
                  className={`gcx-tab${tab === k ? ' is-on' : ''}`}
                  onClick={() => pick(k)}
                >
                  {label}
                  {k === 'markets' && g.markets.length > 0 && (
                    <span className="sg-count np-num">
                      {g.markets.reduce((s, x) => s + x.markets.length, 0)}
                    </span>
                  )}
                </button>
              ))}
            </nav>

            {tab === 'overview' && (
              <>
                <Moneyline g={g} f={f} />
                <Elsewhere g={g} f={f} />
                <TwoColumns title="Last five" g={g} render={(k) => <FormList games={g.recent[k]} />} />
                {(g.injuries.home.length > 0 || g.injuries.away.length > 0) && (
                  <TwoColumns
                    title="Injuries"
                    g={g}
                    render={(k) =>
                      g.injuries[k].length ? (
                        <ul className="sg-list">
                          {g.injuries[k].map((x, i) => (
                            <li key={i}>
                              <span>
                                {x.player}
                                {x.position && <em> {x.position}</em>}
                              </span>
                              <span className="sg-list-v">
                                {x.status}
                                {x.detail ? ` · ${x.detail}` : ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <span className="gc-quiet">None reported.</span>
                      )
                    }
                  />
                )}
              </>
            )}

            {tab === 'markets' &&
              (g.markets.length ? (
                <>
                  <p className="gc-quiet sg-mkts-lede">
                    <VenueLogo venue="polymarket" size={13} /> Every other market Polymarket
                    lists on this game, at the price you&apos;d pay right now, most traded first.
                  </p>
                  {g.markets.map((grp) => (
                    <MarketGroup key={grp.name} group={grp} f={f} />
                  ))}
                </>
              ) : (
                <p className="gc-quiet sg-mkts-lede">
                  Polymarket lists no other markets on this game right now.
                </p>
              ))}

            {tab === 'stats' && (
              <>
                {g.stats.length > 0 && (
                  <section className="gc-section">
                    <h2 className="gc-h2">{g.state === 'pre' ? 'Season so far' : 'This game'}</h2>
                    <div className="sg-stats">
                      <div className="sg-stat is-head">
                        <span className="np-num">{g.away.abbr || g.away.short}</span>
                        <span />
                        <span className="np-num">{g.home.abbr || g.home.short}</span>
                      </div>
                      {g.stats.map((s) => (
                        <div key={s.label} className="sg-stat">
                          <span className="np-num">{s.away}</span>
                          <span className="sg-stat-k">{s.label}</span>
                          <span className="np-num">{s.home}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}
                {(g.leaders.home.length > 0 || g.leaders.away.length > 0) && (
                  <TwoColumns
                    title="Leaders"
                    g={g}
                    render={(k) => (
                      <ul className="sg-list">
                        {g.leaders[k].map((l, i) => (
                          <li key={i}>
                            <span>
                              {l.player}
                              {l.position && <em> {l.position}</em>}
                              <small>{l.category}</small>
                            </span>
                            <span className="sg-list-v np-num">{l.value}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  />
                )}
                {!g.stats.length && !g.leaders.home.length && !g.leaders.away.length && (
                  <p className="gc-quiet">ESPN has no numbers for this game yet.</p>
                )}
              </>
            )}

            <div className="gc-actions">
              <button
                className="gc-action"
                onClick={() => toggle(id, { home: g.home.short, away: g.away.short })}
              >
                {watched ? '★ In watchlist' : '☆ Add to watchlist'}
              </button>
              {(
                [
                  ['polymarket', g.pmUrl],
                  ['kalshi', g.kalshiUrl],
                ] as [Venue, string | null][]
              ).map(([v, url]) =>
                url ? (
                  <a key={v} className="gc-action sg-open" href={url} target="_blank" rel="noopener noreferrer">
                    <VenueLogo venue={v} size={14} title="" /> Open on {VENUE_NAME[v]} ↗
                  </a>
                ) : null
              )}
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
