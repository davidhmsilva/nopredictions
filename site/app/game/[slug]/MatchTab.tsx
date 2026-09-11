'use client'

// The match itself, from ESPN: what happened when, who is playing where, the
// box score, the commentary and the table.

import { useMemo, useState } from 'react'
import type { EspnEvent, EspnMatch, EspnPlayer, EspnSide } from '../../lib/espnMatch'
import type { LiveStats } from '../../lib/gamecenter'
import type { Momentum } from '../../lib/momentum'
import { MomentumChart } from './Insights'
import { dayMonth, shortName } from './fmt'

const ICON: Record<EspnEvent['kind'], string> = {
  goal: '⚽',
  penalty: '⚽',
  'own-goal': '⚽',
  'pen-miss': '✕',
  yellow: '🟨',
  red: '🟥',
  sub: '⇄',
  period: '',
}

// ── timeline ─────────────────────────────────────────────────────────────────

function Timeline({ espn }: { espn: EspnMatch }) {
  const [hover, setHover] = useState<number | null>(null)
  const events = espn.events.filter((e) => e.kind !== 'period')
  if (!events.length) return null
  const maxT = Math.max(90 * 60, ...events.map((e) => e.t))
  const left = (t: number) => `${Math.min(100, (t / maxT) * 100)}%`
  const important = events.filter((e) => e.kind !== 'sub')

  return (
    <section className="gc-section">
      <h2 className="gc-h2">Timeline</h2>
      <div className="gcx-tl" onMouseLeave={() => setHover(null)}>
        <div className="gcx-tl-axis" />
        <span className="gcx-tl-ht" style={{ left: left(45 * 60) }}>HT</span>
        {events.map((e, i) => (
          <button
            key={i}
            className={`gcx-tl-mark gcx-tl-${e.side ?? 'none'} gcx-tl-${e.kind}${hover === i ? ' is-on' : ''}`}
            style={{ left: left(e.t) }}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            aria-label={`${e.minute} ${e.text}`}
          >
            {ICON[e.kind]}
          </button>
        ))}
      </div>
      <div className="gcx-tl-read">
        {hover != null ? (
          <><b className="gc-mono">{events[hover].minute}</b> {events[hover].text}</>
        ) : (
          <span className="gcx-dim">Hover a marker for the moment.</span>
        )}
      </div>

      <ul className="gcx-events">
        {important.map((e, i) => (
          <li key={i} className={`gcx-ev gcx-ev-${e.side ?? 'none'}`}>
            <span className="gcx-ev-min gc-mono">{e.minute}</span>
            <span className="gcx-ev-ico">{ICON[e.kind]}</span>
            <span className="gcx-ev-who">
              {e.player ?? e.text}
              {e.kind === 'own-goal' && <em> (own goal)</em>}
              {e.kind === 'penalty' && <em> (pen)</em>}
              {e.other && ['goal', 'penalty'].includes(e.kind) && <em> · assist {e.other}</em>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── the pitch ────────────────────────────────────────────────────────────────

// Lines from the goalkeeper out, read from ESPN's position codes — its
// formationPlace numbering is not ordered by line, so it only breaks ties.
function lineOf(pos: string | null): number {
  const p = (pos ?? '').toUpperCase()
  if (p === 'G' || p === 'GK') return 0
  if (/^(CD|CB|SW|LB|RB|LWB|RWB|D)\b|^CD-|^D-/.test(p)) return 1
  if (/^DM/.test(p)) return 2
  if (/^AM|^LW|^RW/.test(p)) return 4
  if (/^(F|CF|ST|S|LF|RF)\b|^CF-|^F-/.test(p)) return 5
  return 3
}

function lateral(pos: string | null): number {
  const p = (pos ?? '').toUpperCase()
  if (/-L$|^L[A-Z]*$|^LB|^LM|^LW|^LF|^LWB/.test(p)) return -1
  if (/-R$|^R[A-Z]*$|^RB|^RM|^RW|^RF|^RWB/.test(p)) return 1
  return 0
}

function formationLines(side: EspnSide): EspnPlayer[][] {
  const xi = side.starters
  if (xi.length !== 11) return []
  if (xi.every((p) => p.pos)) {
    const lines = new Map<number, EspnPlayer[]>()
    for (const p of xi) {
      const l = lineOf(p.pos)
      lines.set(l, [...(lines.get(l) ?? []), p])
    }
    return Array.from(lines.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([, ps]) => ps.sort((a, b) => lateral(a.pos) - lateral(b.pos) || a.place - b.place))
  }
  // No position codes: fall back to the formation string, filled in place order.
  const shape = (side.formation ?? '').split('-').map((x) => parseInt(x, 10)).filter((n) => n > 0)
  if (shape.reduce((s, n) => s + n, 0) !== 10) return []
  const sorted = [...xi].sort((a, b) => a.place - b.place)
  const out: EspnPlayer[][] = [[sorted[0]]]
  let i = 1
  for (const n of shape) {
    out.push(sorted.slice(i, i + n))
    i += n
  }
  return out
}

function Pitch({ espn, onPick, picked }: {
  espn: EspnMatch
  onPick: (p: { p: EspnPlayer; side: 'home' | 'away' } | null) => void
  picked: EspnPlayer | null
}) {
  const place = (side: EspnSide, which: 'home' | 'away') => {
    const lines = formationLines(side)
    const n = lines.length
    return lines.flatMap((line, li) =>
      line.map((p, pi) => {
        // Home attacks left→right, so its left flank is the top of the pitch;
        // the away side attacks right→left, so its left flank is the bottom.
        const depth = n > 1 ? li / (n - 1) : 0
        const x = which === 'home' ? 5 + depth * 40 : 95 - depth * 40
        const slot = (pi + 1) / (line.length + 1)
        const y = which === 'home' ? slot * 100 : (1 - slot) * 100
        return { p, x, y, which }
      })
    )
  }
  const dots = [...place(espn.home, 'home'), ...place(espn.away, 'away')]
  if (!dots.length) return null

  return (
    <div className="gcx-pitch-wrap">
      <div className="gcx-pitch">
        <div className="gcx-pitch-lines">
          <span className="gcx-pitch-half" />
          <span className="gcx-pitch-circle" />
          <span className="gcx-pitch-box gcx-pitch-box-l" />
          <span className="gcx-pitch-box gcx-pitch-box-r" />
        </div>
        {dots.map(({ p, x, y, which }) => (
          <button
            key={`${which}-${p.id}`}
            className={`gcx-player gcx-player-${which}${picked?.id === p.id ? ' is-on' : ''}${p.subOut ? ' is-off' : ''}`}
            style={{
              left: `${x}%`,
              top: `${y}%`,
              ['--kit' as string]: (which === 'home' ? espn.home.color : espn.away.color) ?? undefined,
            }}
            onClick={() => onPick(picked?.id === p.id ? null : { p, side: which })}
            title={p.name}
          >
            <span className="gcx-player-num">{p.jersey}</span>
            <span className="gcx-player-name">
              {p.short}
              {p.goals > 0 && <i> {'⚽'.repeat(Math.min(p.goals, 3))}</i>}
              {p.red > 0 ? <i> 🟥</i> : p.yellow > 0 ? <i> 🟨</i> : null}
              {p.subOut && <i className="gcx-sub-off"> ↓{p.subOut}</i>}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function PlayerCard({ p }: { p: EspnPlayer }) {
  const rows: Array<[string, string | number]> = [
    ['Position', p.pos ?? '—'],
    ['Goals', p.goals],
    ['Shots', `${p.shots} (${p.shotsOn} on target)`],
    ['Fouls', p.fouls],
  ]
  if (p.saves) rows.push(['Saves', p.saves])
  if (p.subIn) rows.push(['On', p.subIn])
  if (p.subOut) rows.push(['Off', p.subOut])
  return (
    <div className="gcx-player-card">
      <strong>#{p.jersey} {p.name}</strong>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd className="gc-mono">{v}</dd></div>
        ))}
      </dl>
    </div>
  )
}

function Lineups({ espn }: { espn: EspnMatch }) {
  const [picked, setPicked] = useState<{ p: EspnPlayer; side: 'home' | 'away' } | null>(null)
  const has = espn.home.starters.length === 11 || espn.away.starters.length === 11

  if (!has) {
    return (
      <section className="gc-section">
        <h2 className="gc-h2">Line-ups</h2>
        <div className="gc-nothing">
          <strong>Not announced yet.</strong>
          <span>They usually land about an hour before kick-off, and this page picks them up on its own.</span>
        </div>
      </section>
    )
  }

  const bench = (side: EspnSide) => side.bench.filter((p) => p.subIn)

  return (
    <section className="gc-section">
      <h2 className="gc-h2">Line-ups</h2>
      <div className="gcx-form-row">
        <span>{shortName(espn.home.name)} <b className="gc-mono">{espn.home.formation ?? ''}</b></span>
        <span className="gc-r"><b className="gc-mono">{espn.away.formation ?? ''}</b> {shortName(espn.away.name)}</span>
      </div>
      <Pitch espn={espn} picked={picked?.p ?? null} onPick={setPicked} />
      {picked ? <PlayerCard p={picked.p} /> : <p className="gc-chart-note">Tap a player for his match numbers.</p>}

      {(bench(espn.home).length > 0 || bench(espn.away).length > 0) && (
        <div className="gcx-teams gcx-bench">
          {[espn.home, espn.away].map((side) => (
            <ul key={side.id}>
              {bench(side).map((p) => (
                <li key={p.id}>
                  <span className="gc-mono gcx-dim">↑{p.subIn}</span> {p.short}
                  {p.goals > 0 && ' ⚽'}
                </li>
              ))}
            </ul>
          ))}
        </div>
      )}
    </section>
  )
}

// ── box score ────────────────────────────────────────────────────────────────

function BoxScore({ espn, fallback }: { espn: EspnMatch | null; fallback: LiveStats | null }) {
  const rows = useMemo(() => {
    if (espn?.stats.length) return espn.stats.map((s) => ({ label: s.label, home: s.home, away: s.away, h: s.h, a: s.a }))
    if (!fallback) return []
    const r = [
      { label: 'Possession', home: `${fallback.homePossession ?? 0}%`, away: `${fallback.awayPossession ?? 0}%`, h: fallback.homePossession, a: fallback.awayPossession },
      { label: 'Shots', home: String(fallback.homeShotsTotal), away: String(fallback.awayShotsTotal), h: fallback.homeShotsTotal, a: fallback.awayShotsTotal },
      { label: 'On target', home: String(fallback.homeShotsOn), away: String(fallback.awayShotsOn), h: fallback.homeShotsOn, a: fallback.awayShotsOn },
      { label: 'Corners', home: String(fallback.homeCorners), away: String(fallback.awayCorners), h: fallback.homeCorners, a: fallback.awayCorners },
    ]
    if (fallback.homeXg != null && fallback.awayXg != null) {
      r.splice(1, 0, { label: 'xG', home: fallback.homeXg.toFixed(2), away: fallback.awayXg.toFixed(2), h: fallback.homeXg, a: fallback.awayXg })
    }
    return r
  }, [espn, fallback])

  if (!rows.length) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Match stats</h2>
      <div className="gc-mom">
        {rows.map((r) => {
          const total = (r.h ?? 0) + (r.a ?? 0)
          // 0-0 is an empty rail, not a 50/50 bar claiming a balance nothing established.
          const hp = total > 0 ? ((r.h ?? 0) / total) * 100 : 0
          return (
            <div key={r.label} className="gc-mom-row">
              <span className="gc-mom-v np-num">{r.home}</span>
              <div className="gc-mom-mid">
                <span className="gc-mom-k">{r.label}</span>
                <div className="gc-mom-rail">
                  <span className="gc-mom-fill" style={{ width: `${hp}%` }} />
                </div>
              </div>
              <span className="gc-mom-v gc-mom-v-away np-num">{r.away}</span>
            </div>
          )
        })}
      </div>
      <p className="gc-chart-note">{espn?.stats.length ? 'From ESPN.' : 'From api-football.'}</p>
    </section>
  )
}

// ── commentary, table, info ──────────────────────────────────────────────────

function Commentary({ espn }: { espn: EspnMatch }) {
  const [all, setAll] = useState(false)
  if (!espn.commentary.length) return null
  const shown = all ? espn.commentary : espn.commentary.slice(0, 10)
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Commentary</h2>
      <ul className="gcx-comm">
        {shown.map((c, i) => (
          <li key={i}>
            <span className="gc-mono gcx-dim">{c.minute}</span>
            <span>{c.text}</span>
          </li>
        ))}
      </ul>
      {espn.commentary.length > 10 && (
        <button className="gc-more" onClick={() => setAll((v) => !v)}>
          {all ? 'show fewer' : `show all ${espn.commentary.length}`}
        </button>
      )}
    </section>
  )
}

function Table({ espn }: { espn: EspnMatch }) {
  const [all, setAll] = useState(false)
  const t = espn.table
  if (!t || t.rows.length < 3) return null
  // Around the two teams by default; the whole table behind a click.
  const marks = t.rows.map((r, i) => (r.mark ? i : -1)).filter((i) => i >= 0)
  const keep = new Set<number>()
  for (const m of marks) for (let i = m - 2; i <= m + 2; i++) keep.add(i)
  const rows = all || !marks.length ? t.rows : t.rows.filter((_, i) => keep.has(i))

  return (
    <section className="gc-section">
      <h2 className="gc-h2">{t.name}</h2>
      <div className="gcx-scroll">
        <table className="gcx-table gcx-standings">
          <thead>
            <tr><th>#</th><th>Team</th><th className="gc-r">P</th><th className="gc-r">W</th><th className="gc-r">D</th><th className="gc-r">L</th><th className="gc-r">GD</th><th className="gc-r">Pts</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.team} className={r.mark ? `is-${r.mark}` : ''}>
                <td className="gc-mono">{r.rank}</td>
                <td>{r.team}</td>
                <td className="gc-r gc-mono">{r.gp}</td>
                <td className="gc-r gc-mono">{r.w}</td>
                <td className="gc-r gc-mono">{r.d}</td>
                <td className="gc-r gc-mono">{r.l}</td>
                <td className="gc-r gc-mono">{r.gd > 0 ? `+${r.gd}` : r.gd}</td>
                <td className="gc-r gc-mono"><b>{r.pts}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {marks.length > 0 && t.rows.length > rows.length && (
        <button className="gc-more" onClick={() => setAll(true)}>show the whole table</button>
      )}
      {all && <button className="gc-more" onClick={() => setAll(false)}>around these two only</button>}
    </section>
  )
}

function FormStrip({ espn }: { espn: EspnMatch }) {
  if (!espn.home.form.length && !espn.away.form.length) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Last five, all competitions</h2>
      <div className="gcx-teams">
        {[espn.home, espn.away].map((side) => (
          <ul key={side.id} className="gcx-games">
            {side.form.map((g, i) => (
              <li key={i}>
                <span className="gcx-games-date gc-mono">{g.date ? dayMonth(g.date) : ''}</span>
                <span className="gcx-games-venue">{g.venue === 'H' ? 'v' : '@'}</span>
                <span className="gcx-games-opp" title={g.competition}>{g.opponent}</span>
                <span className="gcx-games-ft gc-mono">{g.score}</span>
                <span className={`gcx-pill gcx-pill-${g.result}`}>{g.result}</span>
              </li>
            ))}
          </ul>
        ))}
      </div>
      <p className="gc-chart-note">From ESPN — includes friendlies and cups our database does not hold.</p>
    </section>
  )
}

export function MatchTab({ espn, loading, momentum, liveStats, home, away }: {
  espn: EspnMatch | null
  loading: boolean
  momentum: Momentum | null
  liveStats: LiveStats | null
  home: string
  away: string
}) {
  if (loading && !espn) {
    return <div className="gc-loading"><span className="scan-spinner" /> finding the match…</div>
  }

  return (
    <>
      {espn && <Timeline espn={espn} />}
      {momentum && <MomentumChart m={momentum} espn={espn} home={home} away={away} />}
      <BoxScore espn={espn} fallback={liveStats} />
      {espn && <Lineups espn={espn} />}
      {espn && <Commentary espn={espn} />}
      {espn && <Table espn={espn} />}
      {espn && <FormStrip espn={espn} />}
      {!espn && (
        <div className="gc-nothing">
          <strong>No match feed for this fixture.</strong>
          <span>
            Line-ups, the timeline and the table come from ESPN, which covers about fifty
            competitions. This one either is not among them or is not on today&apos;s schedule there.
          </span>
        </div>
      )}
      {espn && (
        <p className="gc-chart-note">
          Match data from{' '}
          <a href={espn.url} target="_blank" rel="noopener noreferrer">ESPN</a>
          {espn.swapped && ` · ESPN lists ${away} as the home side`}.
        </p>
      )}
    </>
  )
}
