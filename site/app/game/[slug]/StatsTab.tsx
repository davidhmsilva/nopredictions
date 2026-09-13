'use client'

// Team form from our database: the two sides side by side, their recent
// matches with the closing price each was played at, head to head, and every
// run that clears the rarity bar.

import { useState } from 'react'
import type { FormStats, H2HGame, MarketRecord, TeamContext, TeamForm, TeamGame } from '../../lib/teamform'
import type { EspnMatch } from '../../lib/espnMatch'
import { StreakChip } from './Insights'
import { dayMonth, dayMonthYear, frac, oddsDec, rateOf, shortName, type Count } from './fmt'
import { useOddsFormat } from '../../lib/display'

type SplitKey = 'last5' | 'last10' | 'venue' | 'season'

const SPLITS: Array<[SplitKey, string]> = [
  ['last5', 'Last 5'],
  ['last10', 'Last 10'],
  ['venue', 'Home / away'],
  ['season', 'Since 1 Jul'],
]

interface Row {
  label: string
  get: (s: FormStats) => Count | number | null
  fmt?: (v: number) => string
  /** For averages: bars compare the two values, not a share of games. */
  avg?: boolean
}

const GROUPS: Array<{ title: string; rows: Row[] }> = [
  {
    title: 'Results',
    rows: [
      { label: 'Won', get: (s) => ({ k: s.w, n: s.games }) },
      { label: 'Drew', get: (s) => ({ k: s.d, n: s.games }) },
      { label: 'Lost', get: (s) => ({ k: s.l, n: s.games }) },
      { label: 'Scored per game', get: (s) => (s.games ? s.gf : null), avg: true, fmt: (v) => v.toFixed(2) },
      { label: 'Conceded per game', get: (s) => (s.games ? s.ga : null), avg: true, fmt: (v) => v.toFixed(2) },
      { label: 'Clean sheet', get: (s) => s.cleanSheet },
      { label: 'Failed to score', get: (s) => s.failedToScore },
    ],
  },
  {
    title: 'Goals in their matches',
    rows: [
      { label: 'Over 1.5', get: (s) => s.o15 },
      { label: 'Over 2.5', get: (s) => s.o25 },
      { label: 'Over 3.5', get: (s) => s.o35 },
      { label: 'Both teams scored', get: (s) => s.btts },
      { label: 'Goal in the 2nd half', get: (s) => s.shGoal },
    ],
  },
  {
    title: 'First half',
    rows: [
      { label: 'Goal before half time', get: (s) => s.htGoal },
      { label: '1st half over 1.5', get: (s) => s.htO15 },
      { label: 'Scored in the 1st half', get: (s) => s.scored1h },
      { label: 'Conceded in the 1st half', get: (s) => s.conceded1h },
      { label: 'Ahead at half time', get: (s) => s.ledAtHt },
      { label: '1st-half goals per game', get: (s) => s.avgHtGoals, avg: true, fmt: (v) => v.toFixed(2) },
      { label: 'Share of goals before HT', get: (s) => s.share1h, avg: true, fmt: (v) => `${Math.round(v * 100)}%` },
    ],
  },
]

function Cell({ v, fmt }: { v: Count | number | null; fmt?: (x: number) => string }) {
  if (v == null) return <span className="gcx-cmp-v gcx-dim">—</span>
  if (typeof v === 'number') return <span className="gcx-cmp-v gc-mono">{fmt ? fmt(v) : v}</span>
  const r = rateOf(v)
  return (
    <span className="gcx-cmp-v gc-mono">
      {r == null ? '—' : `${Math.round(r * 100)}%`}
      <i>{frac(v)}</i>
    </span>
  )
}

function CompareTable({ home, away, split, swapped }: {
  home: TeamForm | null
  away: TeamForm | null
  split: SplitKey
  swapped: boolean
}) {
  // The venue split is each side's REAL venue: ESPN's home/away when it has
  // spoken, Polymarket's title order only when nothing better exists.
  const pick = (t: TeamForm | null, side: 'home' | 'away'): FormStats | null => {
    if (!t) return null
    if (split !== 'venue') return t.splits[split]
    const atHome = side === 'home' ? !swapped : swapped
    return atHome ? t.splits.home10 : t.splits.away10
  }
  const hs = pick(home, 'home')
  const as = pick(away, 'away')

  return (
    <div className="gcx-cmp">
      <div className="gcx-cmp-head">
        <span>{home ? shortName(home.name) : '—'}{split === 'venue' && <em>{swapped ? ' away' : ' at home'}</em>}<i>{hs ? `${hs.games} games` : ''}</i></span>
        <span />
        <span className="gc-r">{away ? shortName(away.name) : '—'}{split === 'venue' && <em>{swapped ? ' at home' : ' away'}</em>}<i>{as ? `${as.games} games` : ''}</i></span>
      </div>
      {GROUPS.map((g) => (
        <div key={g.title} className="gcx-cmp-group">
          <div className="gcx-cmp-title">{g.title}</div>
          {g.rows.map((r) => {
            const hv = hs ? r.get(hs) : null
            const av = as ? r.get(as) : null
            const val = (v: Count | number | null) =>
              v == null ? null : typeof v === 'number' ? v : rateOf(v)
            const h = val(hv)
            const a = val(av)
            // Rates fill their own rail out of 100%; averages share one rail.
            const hw = r.avg ? (h != null && a != null && h + a > 0 ? (h / (h + a)) * 100 : 0) : (h ?? 0) * 100
            const aw = r.avg ? (h != null && a != null && h + a > 0 ? (a / (h + a)) * 100 : 0) : (a ?? 0) * 100
            return (
              <div key={r.label} className="gcx-cmp-row">
                <Cell v={hv} fmt={r.fmt} />
                <div className="gcx-cmp-mid">
                  <span className="gcx-cmp-k">{r.label}</span>
                  <div className="gcx-cmp-rails">
                    <div className="gcx-rail gcx-rail-home"><span style={{ width: `${hw}%` }} /></div>
                    <div className="gcx-rail gcx-rail-away"><span style={{ width: `${aw}%` }} /></div>
                  </div>
                </div>
                <Cell v={av} fmt={r.fmt} />
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function MarketLine({ label, r }: { label: string; r: MarketRecord | null }) {
  if (!r) return null
  const diff = r.actual - r.expected
  return (
    <div className="gcx-mkt-line">
      <span>{label}</span>
      <b className="gc-mono">{r.actual}</b>
      <span className="gcx-dim">vs {r.expected.toFixed(1)} priced in, {r.games} games</span>
      <span className={`gc-mono ${Math.abs(diff) < 1 ? 'gcx-dim' : diff > 0 ? 'gc-pos' : 'gcx-neg'}`}>
        {diff > 0 ? '+' : ''}{diff.toFixed(1)}
      </span>
    </div>
  )
}

function Pill({ r }: { r: 'W' | 'D' | 'L' | string }) {
  return <span className={`gcx-pill gcx-pill-${r}`}>{r}</span>
}

function GameList({ games }: { games: TeamGame[] }) {
  const [all, setAll] = useState(false)
  const oddsFmt = useOddsFormat()
  const shown = all ? games : games.slice(0, 8)
  return (
    <>
      <ul className="gcx-games">
        {shown.map((g, i) => {
          const tot = g.gf + g.ga
          return (
            <li key={i}>
              <span className="gcx-games-date gc-mono">{dayMonth(g.date)}</span>
              <span className="gcx-games-venue">{g.venue === 'H' ? 'v' : '@'}</span>
              <span className="gcx-games-opp" title={g.league}>{g.opponent}</span>
              <span className="gcx-games-ht gc-mono">{g.hf != null ? `${g.hf}-${g.ha}` : ''}</span>
              <span className="gcx-games-ft gc-mono">{g.gf}-{g.ga}</span>
              <Pill r={g.result} />
              <span className="gcx-games-tags">
                <i className={tot > 2 ? 'on' : ''} title="Over 2.5">O</i>
                <i className={g.gf > 0 && g.ga > 0 ? 'on' : ''} title="Both teams scored">B</i>
              </span>
              <span className="gcx-games-odds gc-mono" title={g.oddsSource === 'pinnacle' ? 'Pinnacle closing odds on this team' : 'Market-average closing odds on this team'}>
                {g.odds ? oddsDec(g.odds, oddsFmt) : ''}
              </span>
            </li>
          )
        })}
      </ul>
      {games.length > 8 && (
        <button className="gc-more" onClick={() => setAll((v) => !v)}>
          {all ? 'show fewer' : `show all ${games.length}`}
        </button>
      )}
    </>
  )
}

function H2H({ games, home, away }: { games: H2HGame[]; home: string; away: string }) {
  if (!games.length) return null
  const winsOf = (name: string) =>
    games.filter((g) => (g.home === name ? g.hs > g.as : g.away === name ? g.as > g.hs : false)).length
  const draws = games.filter((g) => g.hs === g.as).length
  const overs = games.filter((g) => g.hs + g.as > 2).length
  const btts = games.filter((g) => g.hs > 0 && g.as > 0).length
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Head to head</h2>
      <div className="gcx-h2h-sum">
        <span><b className="gc-mono">{winsOf(home)}</b>{shortName(home)}</span>
        <span><b className="gc-mono">{draws}</b>draws</span>
        <span><b className="gc-mono">{winsOf(away)}</b>{shortName(away)}</span>
        <span className="gcx-dim">over 2.5 in {overs} of {games.length} · both scored in {btts}</span>
      </div>
      <ul className="gcx-games gcx-h2h">
        {games.map((g, i) => (
          <li key={i}>
            <span className="gcx-games-date gc-mono">{dayMonthYear(g.date)}</span>
            <span className="gcx-h2h-teams">{g.home} <b className="gc-mono">{g.hs}-{g.as}</b> {g.away}</span>
            <span className="gcx-games-ht gc-mono">{g.hht != null ? `HT ${g.hht}-${g.aht}` : ''}</span>
            <span className="gcx-dim gcx-h2h-lg">{g.league}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function TeamColumn({ t, swapped }: { t: TeamForm; swapped: boolean }) {
  return (
    <div className="gcx-team-col">
      <h3 className="gcx-h3">{t.name} <span className="gcx-dim">{t.league}</span></h3>

      <div className="gcx-mkt">
        <div className="gcx-mkt-title">Against the closing price</div>
        <MarketLine label="Wins" r={t.market.wins} />
        <MarketLine label="Over 2.5" r={t.market.overs} />
        {!t.market.wins && !t.market.overs && <p className="gc-quiet">Too few priced games.</p>}
      </div>

      {t.streaks.length > 0 && (
        <ul className="gcx-streaks gcx-streaks-compact">
          {t.streaks.map((s, i) => <StreakChip key={i} s={s} swapped={swapped} />)}
        </ul>
      )}

      <GameList games={t.games} />
    </div>
  )
}

export function StatsTab({ ctx, loading, espn, home, away }: {
  ctx: TeamContext | null
  loading: boolean
  espn: EspnMatch | null
  home: string
  away: string
}) {
  const [split, setSplit] = useState<SplitKey>('last10')
  const swapped = !!espn?.swapped

  if (loading && !ctx) {
    return <div className="gc-loading"><span className="scan-spinner" /> reading 145,000 matches…</div>
  }
  if (!ctx || (!ctx.home && !ctx.away)) {
    return (
      <div className="gc-nothing">
        <strong>No team history for this fixture.</strong>
        <span>
          {ctx?.resolution.note ??
            'Our database covers 22 European leagues in full, plus MLS, Liga MX and South America through 2025. These clubs are not in it.'}
        </span>
      </div>
    )
  }

  const stale = [ctx.home, ctx.away].some(
    (t) => t?.lastPlayed && Date.now() - new Date(t.lastPlayed).getTime() > 21 * 864e5
  )

  return (
    <>
      <section className="gc-section">
        <div className="gcx-split">
          {SPLITS.map(([k, label]) => (
            <button key={k} className={split === k ? 'is-on' : ''} onClick={() => setSplit(k)}>
              {label}
            </button>
          ))}
        </div>
        <CompareTable home={ctx.home} away={ctx.away} split={split} swapped={swapped} />
        <p className="gc-chart-note">
          From our own database: every match with its half-time score and closing price. League and
          cup games in the competitions we hold; friendlies are not included.
          {stale && ' ⚠️ One of these teams has no match in our data for three weeks — its latest games may not be loaded yet.'}
          {ctx.resolution.note && ` ${ctx.resolution.note}`}
        </p>
      </section>

      <section className="gc-section">
        <h2 className="gc-h2">Recent matches</h2>
        <div className="gcx-teams">
          {ctx.home ? <TeamColumn t={ctx.home} swapped={swapped} /> : <div className="gc-quiet">{home}: not in our database.</div>}
          {ctx.away ? <TeamColumn t={ctx.away} swapped={swapped} /> : <div className="gc-quiet">{away}: not in our database.</div>}
        </div>
        <p className="gc-chart-note">
          <b>O</b> over 2.5 · <b>B</b> both teams scored · the last figure is the closing decimal on
          that team (Pinnacle, else the market average). &quot;Against the closing price&quot; sets what
          happened against what those prices expected — on ten games that is mostly variance.
        </p>
      </section>

      {ctx.home && ctx.away && <H2H games={ctx.h2h} home={ctx.home.name} away={ctx.away.name} />}
    </>
  )
}
