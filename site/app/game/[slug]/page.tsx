'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppNav, AppFooter } from '../../components/AppShell'
import type { GameData, LiveStats, MarketGroup } from '../../lib/gamecenter'
import { tradeable, type Look, type Pulse } from '../../lib/looks'

const WATCHLIST_KEY = 'np_watchlist'

// The user thinks in decimal odds, so every probability on this page carries the
// price. Outside this band the decimal stops describing a bet anyone would
// place — an outcome at 0.001 renders as 1000.00 — so those are labelled.
const SETTLED_BAND = 0.01

function odds(p: number | null | undefined): string {
  if (p == null || p <= 0 || p >= 1) return '—'
  if (p <= SETTLED_BAND) return 'settled ✗'
  if (p >= 1 - SETTLED_BAND) return 'settled ✓'
  return (1 / p).toFixed(2)
}

function pct(p: number | null | undefined): string {
  return p == null ? '—' : `${(p * 100).toFixed(0)}%`
}

function money(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}

function signed(pp: number | null | undefined): string {
  return pp == null ? '—' : `${pp > 0 ? '+' : ''}${pp.toFixed(1)}pp`
}

// ── the state bar ────────────────────────────────────────────────────────────
//
// Everything a companion needs in the three seconds you look away from the
// game: who is playing, what the score is, what minute it is.

function StateBar({ data }: { data: GameData }) {
  const live = data.live
  const board = data.board
  const kickoff = data.kickoff ? new Date(data.kickoff) : null

  // The live feed wins when it is there; the board carries the score when it is
  // not, which is most of the time on smaller competitions.
  const score =
    live
      ? { h: live.homeGoals, a: live.awayGoals, minute: live.minute }
      : board.homeGoals != null && board.awayGoals != null
        ? { h: board.homeGoals, a: board.awayGoals, minute: null }
        : null
  const inPlay = !!live || board.phase === 'live'
  const badge = board.phase === 'finished' ? 'FT' : inPlay ? 'LIVE' : null

  return (
    <div className="gc-bar">
      <div className="gc-bar-main">
        <span className="gc-bar-team">{data.home}</span>
        {score ? (
          <span className="gc-bar-score">
            {score.h}-{score.a}
          </span>
        ) : (
          <span className="gc-bar-vs">vs</span>
        )}
        <span className="gc-bar-team gc-bar-team-a">{data.away}</span>
      </div>

      <div className="gc-bar-meta">
        {badge && (
          <span className={badge === 'FT' ? 'gc-ft-badge' : 'analysis-live-badge'}>{badge}</span>
        )}
        {score?.minute != null ? (
          <span className="gc-bar-minute">{score.minute}&apos;</span>
        ) : inPlay ? (
          <span className="gc-bar-noclock" title={board.evidence ?? undefined}>
            no clock
          </span>
        ) : kickoff ? (
          <span className="gc-bar-ko">
            {kickoff.toLocaleString(undefined, {
              weekday: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        ) : null}
        {data.competition && <span className="gc-bar-comp">{data.competition}</span>}
      </div>
    </div>
  )
}

// ── what just happened ───────────────────────────────────────────────────────

function Pulse({ pulse, stats }: { pulse: Pulse[]; stats: LiveStats | null }) {
  if (!pulse.length && !stats) return null

  return (
    <section className="gc-pulse">
      <div className="gc-eyebrow">Last 20 minutes</div>

      {pulse.length > 0 ? (
        <ul className="gc-pulse-list">
          {pulse.slice(0, 4).map((p, i) => (
            <li key={i} className={p.movePp > 0 ? 'gc-pulse-up' : 'gc-pulse-down'}>
              <span className="gc-pulse-move">{signed(p.movePp)}</span>
              <span className="gc-pulse-what">
                {p.question.split(':').pop()?.trim()} — {p.outcome}
              </span>
              <span className="gc-pulse-price">
                {odds(p.from)} → {odds(p.to)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="gc-quiet">Prices have not moved. Nothing is happening.</p>
      )}

      {stats && <StatStrip s={stats} />}
    </section>
  )
}

function StatStrip({ s }: { s: LiveStats }) {
  type Cell = string | number | null
  const rows: Array<[string, Cell, Cell]> = ([
    ['xG', s.homeXg?.toFixed(2) ?? null, s.awayXg?.toFixed(2) ?? null],
    ['shots on', s.homeShotsOn, s.awayShotsOn],
    ['shots', s.homeShotsTotal, s.awayShotsTotal],
    ['corners', s.homeCorners, s.awayCorners],
    ['possession', s.homePossession != null ? `${s.homePossession}%` : null,
      s.awayPossession != null ? `${s.awayPossession}%` : null],
  ] as Array<[string, Cell, Cell]>).filter(([, h, a]) => h != null || a != null)

  if (!rows.length) return null

  return (
    <div className="gc-statstrip">
      {rows.map(([label, h, a]) => (
        <span key={label} className="gc-statcell">
          <b>{h ?? '—'}</b>
          <em>{label}</em>
          <b>{a ?? '—'}</b>
        </span>
      ))}
    </div>
  )
}

// ── the shortlist ────────────────────────────────────────────────────────────

const CALL_LABEL: Record<Look['call'], string> = {
  back: 'CHEAP',
  fair: 'FAIR',
  rich: 'EXPENSIVE',
}

function LookRow({ look }: { look: Look }) {
  const [open, setOpen] = useState(false)
  const can = tradeable(look)

  return (
    <li className={`gc-look gc-look-${look.call}${can ? '' : ' gc-look-untradeable'}`}>
      <div className="gc-look-top">
        <span className={`gc-call gc-call-${look.call}`}>{CALL_LABEL[look.call]}</span>
        <span className="gc-look-side">{look.side}</span>
        <span className="gc-look-odds">
          {look.odds.toFixed(2)}
          <i>{pct(look.prob)}</i>
        </span>
      </div>

      <div className="gc-look-nums">
        <span>
          <em>measured</em>
          <b>{look.fairOdds ? look.fairOdds.toFixed(2) : '—'}</b>
          <i>{pct(look.fairProb)}</i>
        </span>
        <span className={look.edgePp != null && look.edgePp > 0 ? 'gc-pos' : 'gc-neg'}>
          <em>edge after fee</em>
          <b>{signed(look.edgePp)}</b>
          <i>fee {look.feePp.toFixed(2)}pp</i>
        </span>
        <span>
          <em>available</em>
          <b>{look.isMid ? 'no book' : money(look.depthUsd)}</b>
          <i>{look.spreadPp != null ? `${look.spreadPp.toFixed(1)}pp wide` : 'mid only'}</i>
        </span>
      </div>

      <p className="gc-look-why">{look.why}</p>

      <div className="gc-look-flags">
        {look.n != null && <span className="gc-flag gc-flag-ok">measured · n={look.n.toLocaleString()}</span>}
        {look.imported && <span className="gc-flag gc-flag-warn">imported rate</span>}
        {look.isMid && <span className="gc-flag gc-flag-warn">not executable</span>}
        {!look.isMid && look.depthUsd != null && look.depthUsd < 100 && (
          <span className="gc-flag gc-flag-warn">thin</span>
        )}
        <button className="gc-why" onClick={() => setOpen((v) => !v)}>
          {open ? 'hide detail' : 'where this comes from'}
        </button>
      </div>

      {open && (
        <ul className="gc-notes">
          {look.detail.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
          <li>{look.market}</li>
        </ul>
      )}
    </li>
  )
}

function Looks({ data }: { data: GameData }) {
  const looks = data.looks ?? []
  // A price you cannot pay is not a look. Those stay collapsed rather than
  // ranking alongside prices with a real book behind them.
  const live = looks.filter(tradeable)
  const mids = looks.filter((l) => !tradeable(l))
  const backs = live.filter((l) => l.call === 'back')
  const [showMids, setShowMids] = useState(false)

  return (
    <section className="gc-section">
      <div className="gc-eyebrow">Worth a look</div>

      {live.length === 0 ? (
        <div className="gc-nothing">
          <strong>No measured read on this fixture yet.</strong>
          <span>{NO_READ_REASON(data)}</span>
        </div>
      ) : (
        <>
          {backs.length === 0 && (
            <div className="gc-verdict">
              <strong>Nothing is cheap right now.</strong>
              <span>
                Every price below sits inside the taker fee of the rate we measured. That is the
                normal state of this market — the prices are shown anyway, because &quot;the market has
                this right&quot; is the answer to most questions worth asking mid-match.
              </span>
            </div>
          )}
          {/* Shown whether or not anything is cheap: the comparison IS the
              product. A bettor watching wants "is 1.23 too short?" answered in
              two seconds, and the answer is usually no. */}
          <ul className="gc-looks">
            {live.map((l) => (
              <LookRow key={l.id} look={l} />
            ))}
          </ul>
        </>
      )}

      {mids.length > 0 && (
        <>
          <button className="gc-more" onClick={() => setShowMids((v) => !v)}>
            {showMids
              ? 'hide markets with no order book'
              : `${mids.length} more measured, but with no order book to trade against`}
          </button>
          {showMids && (
            <ul className="gc-looks gc-looks-quiet">
              {mids.map((l) => (
                <LookRow key={l.id} look={l} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

/** Why the shortlist is empty. A companion that goes blank without saying why
 *  is worse than one that says "not yet". */
function NO_READ_REASON(data: GameData): string {
  if (data.board.phase === 'finished') return 'This match is over.'
  if (data.board.phase !== 'live') {
    return (
      'The measured tables are conditioned on a live state — a minute and a score — so they ' +
      'have nothing to say before kick-off. Pre-match, Polymarket sits within 0.10pp of the ' +
      'de-vigged Pinnacle line, which is another way of saying there is nothing here.'
    )
  }
  if (!data.live) {
    return (
      'The match is live but no clock matched it: api-football did not have this fixture in ' +
      'its live feed, and every measured rate here is keyed on the minute. Polymarket\'s own ' +
      'listed start time is not used as a substitute — it ran half an hour early on some ' +
      'leagues and eight hours late on others.'
    )
  }
  if (data.live.minute != null && data.live.minute < 10) {
    return 'Too early — the measured tables start at 10 minutes.'
  }
  return 'Polymarket is not quoting the rungs the measured tables price.'
}

// ── the board, tradeable first ───────────────────────────────────────────────

interface BoardRow {
  question: string
  outcome: string
  ask: number
  spreadPp: number
  depthUsd: number | null
}

function Board({ groups }: { groups: MarketGroup[] }) {
  const [open, setOpen] = useState(false)

  const { rows, quoted } = useMemo(() => {
    const rows: BoardRow[] = []
    let quoted = 0
    for (const g of groups) {
      for (const o of g.outcomes) {
        quoted++
        const b = o.book
        if (b?.ask == null || b?.bid == null) continue
        if (b.ask <= SETTLED_BAND || b.ask >= 1 - SETTLED_BAND) continue
        rows.push({
          question: g.question,
          outcome: o.name,
          ask: b.ask,
          spreadPp: (b.ask - b.bid) * 100,
          depthUsd: b.askDepthUsd,
        })
      }
    }
    rows.sort((a, b) => a.spreadPp - b.spreadPp || (b.depthUsd ?? 0) - (a.depthUsd ?? 0))
    return { rows, quoted }
  }, [groups])

  return (
    <section className="gc-section">
      <div className="gc-eyebrow">The board</div>
      <p className="gc-quiet">
        {rows.length} of {quoted} quoted outcomes have a real order book. The rest are Gamma
        mids — a number, not a price you can pay.
      </p>

      <table className="gc-board">
        <thead>
          <tr>
            <th>Market</th>
            <th>Side</th>
            <th className="gc-r">Ask</th>
            <th className="gc-r">Spread</th>
            <th className="gc-r">Depth</th>
          </tr>
        </thead>
        <tbody>
          {(open ? rows : rows.slice(0, 8)).map((r, i) => (
            <tr key={i}>
              <td className="gc-board-q">{r.question.split(':').pop()?.trim()}</td>
              <td>{r.outcome}</td>
              <td className="gc-r gc-mono">{odds(r.ask)}</td>
              <td className="gc-r gc-mono">{r.spreadPp.toFixed(1)}pp</td>
              <td className="gc-r gc-mono">{money(r.depthUsd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length > 8 && (
        <button className="gc-more" onClick={() => setOpen((v) => !v)}>
          {open ? 'show fewer' : `show all ${rows.length}`}
        </button>
      )}
    </section>
  )
}

// ── the small print, behind a click ──────────────────────────────────────────

function Disclosure({ data }: { data: GameData }) {
  const [open, setOpen] = useState(false)

  return (
    <section className="gc-section">
      <button className="gc-more" onClick={() => setOpen((v) => !v)}>
        {open ? 'hide' : 'what this page cannot tell you'}
      </button>
      {open && (
        <ul className="gc-notes">
          <li>
            <strong>This is not a tip.</strong> Every number here is either a price Polymarket
            is quoting right now or a rate measured on historical matches. Nothing on this page
            is a prediction, and a measured rate is not a guarantee about this match.
          </li>
          <li>
            <strong>No model output, deliberately.</strong> On 6 of 6 outcome groups Polymarket&apos;s
            price beat our own model on Brier score, so a &quot;the model likes this&quot; badge would be
            selling the one thing we measured as not working.
          </li>
          <li>
            <strong>Prices are asks, not mids.</strong> The mid is the number that made a paper
            strategy book +141% where the same 15 decisions returned +3.4% live.
          </li>
          <li>
            <strong>Edges are shown after Polymarket&apos;s taker fee</strong> — shares × 0.05 × p ×
            (1−p), verified to 0.0001% on 87,000 real fills. It peaks at 1.25pp around even money.
          </li>
          {data.board.evidence && (
            <li>
              <strong>Score source:</strong> {data.live ? 'api-football live feed' : data.board.evidence}.
            </li>
          )}
          {data.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function GamePage({ params }: { params: { slug: string } }) {
  const [data, setData] = useState<GameData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [watched, setWatched] = useState(false)

  const slug = params.slug

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/game?slug=${encodeURIComponent(slug)}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`)
      setData(body)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    load()
  }, [load])

  // Refresh while the match is live. 30s matches how long the live feed is
  // cached server-side, so a faster poll would only re-serve the same numbers.
  const isLive = data?.board.phase === 'live'
  useEffect(() => {
    if (!isLive) return
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [isLive, load])

  useEffect(() => {
    try {
      const list = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]') as string[]
      setWatched(list.includes(slug))
    } catch {
      /* a corrupt watchlist is not worth a broken page */
    }
  }, [slug])

  function toggleWatch() {
    try {
      const list = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]') as string[]
      const next = list.includes(slug) ? list.filter((s) => s !== slug) : [...list, slug]
      localStorage.setItem(WATCHLIST_KEY, JSON.stringify(next))
      setWatched(next.includes(slug))
    } catch {
      /* ignore */
    }
  }


  return (
    <div className="scanner-page">
      <AppNav />

      <main className="scanner-main gc-main">
        {loading && (
          <div className="gc-loading">
            <span className="scan-spinner" /> loading fixture…
          </div>
        )}
        {error && <div className="scan-error">Error: {error}</div>}

        {data && (
          <>
            <StateBar data={data} />
            <Looks data={data} />
            <Pulse pulse={data.pulse ?? []} stats={data.live?.stats ?? null} />
            <Board groups={data.groups} />

            <div className="gc-actions">
              <button className="gc-action" onClick={toggleWatch}>
                {watched ? '★ In watchlist' : '☆ Add to watchlist'}
              </button>
              <a className="gc-action" href={data.pmUrl} target="_blank" rel="noopener noreferrer">
                Open on Polymarket ↗
              </a>
              {data.kalshi && (
                <a className="gc-action" href={data.kalshi.url} target="_blank" rel="noopener noreferrer">
                  Open on Kalshi ↗
                </a>
              )}
            </div>

            <Disclosure data={data} />
          </>
        )}
      </main>


      <AppFooter />
    </div>
  )
}
