'use client'

// The panels that exist only because this site holds results joined to closing
// prices: the brief, "priced like this", streaks with their odds of happening,
// and the pressure curve our own agent recorded.

import { useEffect, useMemo, useState } from 'react'
import type { Brief } from '../../lib/matchbrief'
import type { EspnMatch } from '../../lib/espnMatch'
import type { Momentum } from '../../lib/momentum'
import type { PricedLike, PricedLikeBlock } from '../../lib/pricedLike'
import type { Streak, TeamContext } from '../../lib/teamform'
import { odds, oneIn, pct, shortName, signed } from './fmt'

// ── the brief ────────────────────────────────────────────────────────────────

export function BriefCard({ slug }: { slug: string }) {
  const [brief, setBrief] = useState<Brief | null>(null)
  const [reason, setReason] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    fetch(`/api/game/brief?slug=${encodeURIComponent(slug)}`)
      .then((r) => r.json())
      .then((b) => {
        if (!live) return
        setBrief(b.brief ?? null)
        setReason(b.brief ? null : b.reason ?? null)
      })
      .catch(() => live && setReason('The brief could not be loaded.'))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [slug])

  if (!loading && !brief && !reason) return null

  return (
    <section className="gc-section gcx-brief">
      <div className="gcx-brief-head">
        <span className="gcx-brief-tag">Brief</span>
        <span className="gcx-brief-by">
          {brief
            ? `Written by AI from the numbers on this page · ${new Date(brief.writtenAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${brief.inPlay ? 'after kick-off, so no prices — how the sides arrived' : brief.lineups ? 'line-ups in' : 'before line-ups'}`
            : 'Written by AI from the numbers on this page'}
        </span>
      </div>

      {loading && (
        <div className="gcx-brief-skel" aria-label="Writing the brief">
          <span /><span /><span />
        </div>
      )}

      {!loading && brief && (
        <>
          <p className="gcx-brief-headline">{brief.headline}</p>
          <ul className="gcx-brief-points">
            {brief.points.map((p, i) => (
              <li key={i}>
                <strong>{p.title}</strong>
                <span>{p.body}</span>
              </li>
            ))}
          </ul>
          <p className="gcx-brief-caveat">{brief.caveat} Not a tip — nothing on this page is.</p>
        </>
      )}

      {!loading && !brief && reason && <p className="gc-quiet">{reason}</p>}
    </section>
  )
}

// ── priced like this ─────────────────────────────────────────────────────────

function PricedBlock({ title, basis, block, comparable }: {
  title: string
  basis: string
  block: PricedLikeBlock
  comparable: boolean
}) {
  const anyPm = block.lines.some((l) => l.pmAsk != null)
  return (
    <div className="gcx-pl-block">
      <div className="gcx-pl-title">
        <strong>{title}</strong>
        <span>{basis} · {block.n.toLocaleString('en')} matches</span>
      </div>
      <div className="gcx-scroll">
        <table className="gcx-table">
          <thead>
            <tr>
              <th>Market</th>
              <th className="gc-r">Happened</th>
              <th className="gc-r">As odds</th>
              {anyPm && comparable && <th className="gc-r">Polymarket</th>}
              {anyPm && comparable && <th className="gc-r" title="History minus Polymarket's ask, after the taker fee">Gap</th>}
            </tr>
          </thead>
          <tbody>
            {block.lines.map((l) => (
              <tr key={l.key}>
                <td>{l.label}</td>
                <td className="gc-r gc-mono">{pct(l.rate)}</td>
                <td className="gc-r gc-mono">{l.rate ? (1 / l.rate).toFixed(2) : '—'}</td>
                {anyPm && comparable && (
                  <td className={`gc-r gc-mono${l.pmIsMid ? ' gcx-dim' : ''}`} title={l.pmIsMid ? 'No order book — a Gamma mid' : undefined}>
                    {l.pmAsk != null ? odds(l.pmAsk) : '—'}
                  </td>
                )}
                {anyPm && comparable && (
                  <td className={`gc-r gc-mono ${l.gapPp == null ? '' : Math.abs(l.gapPp) < 2 ? 'gcx-dim' : l.gapPp > 0 ? 'gc-pos' : 'gcx-neg'}`}>
                    {signed(l.gapPp)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function PricedLikeCard({ pl }: { pl: PricedLike | null | undefined }) {
  if (!pl || (!pl.totals && !pl.result)) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Matches priced like this one</h2>
      <p className="gc-quiet gcx-lede">
        Every other market, in the matches where Pinnacle — the sharpest book — closed at the same
        price as this one. Built from {pl.source}.
      </p>

      <div className="gcx-pl">
        {pl.totals && (
          <PricedBlock
            title={`Over 2.5 at ${odds(pl.totals.prob)} (${pct(pl.totals.prob)})`}
            basis={`closed ${pct(pl.totals.lo)}–${pct(pl.totals.hi)}${pl.totals.avgGoals ? ` · ${pl.totals.avgGoals.toFixed(2)} goals a game, ${pl.totals.avgHtGoals?.toFixed(2)} before half time` : ''}`}
            block={pl.totals}
            comparable={pl.comparable}
          />
        )}
        {pl.result && (
          <PricedBlock
            title={`Home win at ${odds(pl.result.prob)} (${pct(pl.result.prob)})`}
            basis={`closed ${pct(pl.result.lo)}–${pct(pl.result.hi)}`}
            block={pl.result}
            comparable={pl.comparable}
          />
        )}
      </div>

      <p className="gc-chart-note">
        {pl.comparable ? (
          <>
            <strong>A gap is not an edge.</strong> This history is an average match at this line;
            Polymarket&apos;s price knows who is playing. When the two disagree by more than a couple
            of points, the team numbers in <em>Stats</em> usually say why. Gaps are after
            Polymarket&apos;s taker fee; the price used is the pre-match de-vigged mid.
          </>
        ) : (
          <>
            The match has started, so Polymarket&apos;s live prices are not comparable with these
            pre-match rates. The history is chosen on the Over 2.5 price as it stood before kick-off.
          </>
        )}
      </p>
    </section>
  )
}

// ── streaks ──────────────────────────────────────────────────────────────────

export function StreakChip({ s, team, swapped }: { s: Streak; team?: string; swapped?: boolean }) {
  const scope = s.scope === 'venue' ? (swapped ? null : ' · this venue') : ''
  if (scope === null) return null
  return (
    <li className="gcx-streak">
      {team && <span className="gcx-streak-team">{shortName(team)}</span>}
      <span className="gcx-streak-what">
        {s.label}
        <em>{s.kind === 'run' ? ` ${s.k} in a row` : ` ${s.k} of last ${s.of}`}{scope}</em>
      </span>
      <span className="gcx-streak-odds" title={`League rate ${pct(s.base)}. Chance of a run this long if every match were a coin flip at that rate.`}>
        league {pct(s.base)} · <b>{oneIn(s.chance)}</b>
      </span>
    </li>
  )
}

export function StreakHighlights({ ctx, swapped, limit = 4 }: {
  ctx: TeamContext | null
  swapped: boolean
  limit?: number
}) {
  const all = useMemo(() => {
    if (!ctx) return []
    const out: Array<{ s: Streak; team: string }> = []
    if (ctx.home) for (const s of ctx.home.streaks) out.push({ s, team: ctx.home.name })
    if (ctx.away) for (const s of ctx.away.streaks) out.push({ s, team: ctx.away.name })
    return out
      .filter((x) => !(swapped && x.s.scope === 'venue'))
      .sort((a, b) => a.s.chance - b.s.chance)
  }, [ctx, swapped])

  if (!ctx || all.length === 0) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Runs worth knowing about</h2>
      <ul className="gcx-streaks">
        {all.slice(0, limit).map((x, i) => (
          <StreakChip key={i} s={x.s} team={x.team} swapped={swapped} />
        ))}
      </ul>
      <p className="gc-chart-note">
        &quot;1 in N&quot; is the chance of a run that long at the league&apos;s own rate. We check{' '}
        {ctx.checked} patterns per fixture, so a 1-in-20 run turns up by chance about three times a
        match — only the rarer ones are shown. And they describe; they don&apos;t predict: on 14,365
        held-out matches, recent form added nothing the closing price didn&apos;t already have.
      </p>
    </section>
  )
}

// ── the pressure curve ───────────────────────────────────────────────────────

export function MomentumChart({ m, espn, home, away }: {
  m: Momentum
  espn: EspnMatch | null
  home: string
  away: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 900
  const H = 170
  const MID = H / 2
  const PAD_L = 8
  const PAD_R = 8
  const maxMin = Math.max(90, ...m.points.map((p) => p.minute))
  const peak = Math.max(20, ...m.points.map((p) => Math.max(p.home, p.away)))
  const x = (min: number) => PAD_L + (min / maxMin) * (W - PAD_L - PAD_R)
  const bw = Math.max(2, (W - PAD_L - PAD_R) / maxMin - 2)
  const scale = (v: number) => (v / peak) * (MID - 14)

  // Goals: ESPN's timeline when it has one, else the score changes on our own
  // rows — never both, or a goal is drawn twice.
  const goals = useMemo(() => {
    const ev = espn?.events.filter((e) => ['goal', 'penalty', 'own-goal'].includes(e.kind) && e.side) ?? []
    if (ev.length) {
      return ev.map((e) => ({
        minute: Math.round(e.t / 60),
        // An own goal counts for the other side.
        side: e.kind === 'own-goal' ? (e.side === 'home' ? 'away' : 'home') : (e.side as 'home' | 'away'),
      }))
    }
    const out: Array<{ minute: number; side: 'home' | 'away' }> = []
    for (let i = 1; i < m.points.length; i++) {
      const a = m.points[i - 1]
      const b = m.points[i]
      if (b.homeGoals > a.homeGoals) out.push({ minute: b.minute, side: 'home' })
      if (b.awayGoals > a.awayGoals) out.push({ minute: b.minute, side: 'away' })
    }
    return out
  }, [espn, m])

  const hp = hover != null ? m.points.find((p) => p.minute === hover) : null

  return (
    <section className="gc-section">
      <h2 className="gc-h2">Pressure, minute by minute</h2>
      <div className="gcx-mom-legend">
        <span><i className="gcx-dot gcx-dot-home" />{shortName(home)}</span>
        <span><i className="gcx-dot gcx-dot-away" />{shortName(away)}</span>
        {hp && (
          <span className="gcx-mom-read gc-mono">
            {hp.minute}&apos; · {hp.home.toFixed(0)} v {hp.away.toFixed(0)}
          </span>
        )}
      </div>
      <div className="gc-chart">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="gc-chart-svg"
          role="img"
          aria-label={`Attacking pressure per minute, ${home} above the line and ${away} below`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
            const min = Math.round(((e.clientX - r.left) / r.width) * maxMin)
            const near = m.points.reduce((b, p) => (Math.abs(p.minute - min) < Math.abs(b.minute - min) ? p : b), m.points[0])
            setHover(near.minute)
          }}
        >
          <line x1={x(45)} x2={x(45)} y1={4} y2={H - 4} className="gcx-mom-ht" />
          <line x1={PAD_L} x2={W - PAD_R} y1={MID} y2={MID} className="gc-chart-grid" />
          {m.points.map((p) => (
            <g key={p.minute} opacity={hover == null || hover === p.minute ? 1 : 0.55}>
              <rect x={x(p.minute) - bw / 2} y={MID - scale(p.home)} width={bw} height={scale(p.home)} className="gcx-bar-home" rx={1} />
              <rect x={x(p.minute) - bw / 2} y={MID} width={bw} height={scale(p.away)} className="gcx-bar-away" rx={1} />
            </g>
          ))}
          {goals.map((g, i) => (
            <g key={i} transform={`translate(${x(g.minute)}, ${g.side === 'home' ? 12 : H - 12})`}>
              <circle r={7} className={g.side === 'home' ? 'gcx-goal-home' : 'gcx-goal-away'} />
              <text textAnchor="middle" dy={4} className="gcx-goal-txt">G</text>
            </g>
          ))}
          <text x={x(45)} y={H - 2} textAnchor="middle" className="gc-chart-tick">HT</text>
        </svg>
      </div>
      <p className="gc-chart-note">
        Recorded live by our own agent from the box score — shots, shots in the box, corners and xG
        {m.estimatedXg ? ' (estimated from shots where the feed carries none)' : ''} over a rolling
        15-minute window. It shows who is on top. It does not forecast: we measured the whole reading
        at +0.0008 pseudo-R² over score and minute, and the price already carries about sixteen times
        more.
      </p>
    </section>
  )
}
