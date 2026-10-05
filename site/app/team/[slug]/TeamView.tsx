'use client'

// A club's page. Server-rendered from our own database (see lib/teampage);
// this half only does what needs the reader: their odds format, their clock,
// and the split toggle.

import Link from 'next/link'
import { useState } from 'react'
import type { TeamPageData, UpcomingGame, TableRow } from '../../lib/teampage'
import type { FormStats, TeamGame } from '../../lib/teamform'
import { GROUPS, Cell, MarketLine, Pill } from '../../game/[slug]/StatsTab'
import { StreakChip } from '../../game/[slug]/Insights'
import { odds, oddsDec, shortName } from '../../game/[slug]/fmt'
import { dateText, kickoffText, useMounted, useOddsFormat } from '../../lib/display'
import { teamHref } from '../../lib/teamSlug'
import { VenueLogo } from '../../components/VenueLogo'
import type { Venue } from '../../lib/venues'

// Zone-dependent text renders as UTC on the server and in the reader's zone
// after hydration, so the two passes never disagree.
function When({ iso, year = false }: { iso: string; year?: boolean }) {
  const mounted = useMounted()
  const d = new Date(iso)
  if (mounted) return <>{dateText(d, year)}</>
  return (
    <>
      {d.toLocaleDateString('en-GB', {
        timeZone: 'UTC',
        day: 'numeric',
        month: 'short',
        ...(year ? { year: '2-digit' } : {}),
      })}
    </>
  )
}

function Kickoff({ iso }: { iso: string | null }) {
  const mounted = useMounted()
  if (!iso) return <span className="gcx-dim">time tbc</span>
  return <>{mounted ? kickoffText(new Date(iso)) : new Date(iso).toUTCString().slice(0, 22) + ' UTC'}</>
}

// ── next games with a market ─────────────────────────────────────────────────

function Upcoming({ games, team }: { games: UpcomingGame[] | null; team: string }) {
  const f = useOddsFormat()
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Next with a market</h2>
      {games === null ? (
        <p className="gc-quiet">The board did not answer in time. Reload in a moment.</p>
      ) : games.length === 0 ? (
        <p className="gc-quiet">
          Neither Polymarket nor Kalshi lists a game for {team} right now. Markets usually open a day
          or two before kick-off.
        </p>
      ) : (
        <ul className="tm-next">
          {games.map((g) => (
            <li key={g.slug}>
              <Link href={`/game/${g.slug}`} className="tm-next-row">
                <span className="tm-next-when gc-mono">
                  {g.live ? (
                    <b className="tm-live">
                      LIVE{g.minute != null ? ` ${g.minute}'` : ''}
                      {g.score ? ` · ${g.score.home}-${g.score.away}` : ''}
                    </b>
                  ) : (
                    <Kickoff iso={g.kickoff} />
                  )}
                </span>
                <span className="tm-next-title">
                  {g.title}
                  {g.competition && <em className="gcx-dim"> · {g.competition}</em>}
                </span>
                <span className="tm-next-px gc-mono">
                  <span title={g.home}>{shortName(g.home)} <b>{odds(g.oneX2.home, f)}</b></span>
                  <span>Draw <b>{odds(g.oneX2.draw, f)}</b></span>
                  <span title={g.away}>{shortName(g.away)} <b>{odds(g.oneX2.away, f)}</b></span>
                </span>
                <span className="tm-next-venues">
                  {g.venues.map((v) => <VenueLogo key={v} venue={v as Venue} />)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ── against the closing price ────────────────────────────────────────────────

function Market({ t, row }: { t: TeamPageData; row: TableRow | null }) {
  const pts = row && row.xpts != null && row.priced >= 3 ? row : null
  if (!pts && !t.market.wins && !t.market.overs && !t.seasonOvers) return null
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Against the closing price</h2>
      <div className="gcx-mkt tm-mkt">
        {pts && (
          <div className="gcx-mkt-line">
            <span>Points</span>
            <b className="gc-mono">{pts.ptsPriced}</b>
            <span className="gcx-dim">vs {(pts.xpts as number).toFixed(1)} priced in, {pts.priced} league games this season</span>
            <span className={`gc-mono ${Math.abs(pts.ptsPriced - (pts.xpts as number)) < 2 ? 'gcx-dim' : pts.ptsPriced > (pts.xpts as number) ? 'gc-pos' : 'gcx-neg'}`}>
              {pts.ptsPriced - (pts.xpts as number) > 0 ? '+' : ''}{(pts.ptsPriced - (pts.xpts as number)).toFixed(1)}
            </span>
          </div>
        )}
        <MarketLine label="Wins" r={t.market.wins} />
        <MarketLine label="Over 2.5" r={t.market.overs} />
        {t.seasonOvers && t.market.overs?.games !== t.seasonOvers.games && (
          <MarketLine label="O2.5 season" r={t.seasonOvers} />
        )}
      </div>
      <p className="gc-chart-note">
        What happened set against what the closing prices of the same games expected (Pinnacle,
        else the market average, with the margin removed). Over a handful of games this is mostly
        variance. In the one test we have run, the form features we built added nothing to
        Pinnacle&apos;s closing price on 14,365 matches — so a hot run here is a description, not yet
        a reason to think any price is off, and a softer price than Pinnacle&apos;s close may still be.
      </p>
    </section>
  )
}

// ── splits ───────────────────────────────────────────────────────────────────

const COLS: Array<[keyof TeamPageData['splits'], string]> = [
  ['last5', 'Last 5'],
  ['last10', 'Last 10'],
  ['home10', 'Home 10'],
  ['away10', 'Away 10'],
  ['season', 'Season'],
]

function Splits({ t }: { t: TeamPageData }) {
  const [group, setGroup] = useState(0)
  const g = GROUPS[group]
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Numbers</h2>
      <div className="gcx-split">
        {GROUPS.map((x, i) => (
          <button key={x.title} className={group === i ? 'is-on' : ''} onClick={() => setGroup(i)}>
            {x.title}
          </button>
        ))}
      </div>
      <div className="gcx-scroll">
        <table className="gcx-table tm-splits">
          <thead>
            <tr>
              <th />
              {COLS.map(([k, label]) => (
                <th key={k} className="gc-r">
                  {label}
                  <i className="gcx-dim"> {t.splits[k].games}</i>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {g.rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                {COLS.map(([k]) => {
                  const s: FormStats = t.splits[k]
                  return (
                    <td key={k} className="gc-r">
                      <Cell v={s.games ? r.get(s) : null} fmt={r.fmt} />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="gc-chart-note">
        The small number beside each column is the games it covers. &quot;Season&quot; runs from 1 July.
        League and cup games in the competitions we hold; friendlies are not included.
      </p>
    </section>
  )
}

// ── the table ────────────────────────────────────────────────────────────────

function LeagueTable({ t }: { t: TeamPageData }) {
  const [all, setAll] = useState(false)
  const table = t.table
  if (!table || table.rows.length < 3) return null
  const me = table.rows.findIndex((r) => r.id === t.id)
  const rows = all || me < 0 ? table.rows : table.rows.filter((_, i) => Math.abs(i - me) <= 3)
  const pos = (r: TableRow) => table.rows.indexOf(r) + 1

  return (
    <section className="gc-section">
      <h2 className="gc-h2">
        {table.league} <span className="gcx-dim">{table.season}</span>
      </h2>
      <div className="gcx-scroll">
        <table className="gcx-table gcx-standings">
          <thead>
            <tr>
              <th>#</th><th>Team</th>
              <th className="gc-r">P</th><th className="gc-r">W</th><th className="gc-r">D</th><th className="gc-r">L</th>
              <th className="gc-r">GD</th><th className="gc-r">Pts</th>
              <th className="gc-r" title="Points the closing prices of the same games expected">Priced</th>
              <th className="gc-r" title="Points minus priced points, on the games that had a closing price">±</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const diff = r.xpts != null ? r.ptsPriced - r.xpts : null
              return (
                <tr key={r.id} className={r.id === t.id ? 'is-home' : ''}>
                  <td className="gc-mono">{pos(r)}</td>
                  <td>
                    {r.id === t.id ? <b>{r.team}</b> : <Link href={teamHref(r.id, r.team)} className="tm-link">{r.team}</Link>}
                  </td>
                  <td className="gc-r gc-mono">{r.p}</td>
                  <td className="gc-r gc-mono">{r.w}</td>
                  <td className="gc-r gc-mono">{r.d}</td>
                  <td className="gc-r gc-mono">{r.l}</td>
                  <td className="gc-r gc-mono">{r.gf - r.ga > 0 ? `+${r.gf - r.ga}` : r.gf - r.ga}</td>
                  <td className="gc-r gc-mono"><b>{r.pts}</b></td>
                  <td className="gc-r gc-mono gcx-dim">{r.xpts != null ? r.xpts.toFixed(1) : '—'}</td>
                  <td className={`gc-r gc-mono ${diff == null || Math.abs(diff) < 2 ? 'gcx-dim' : diff > 0 ? 'gc-pos' : 'gcx-neg'}`}>
                    {diff == null ? '—' : `${diff > 0 ? '+' : ''}${diff.toFixed(1)}`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {!all && rows.length < table.rows.length && (
        <button className="gc-more" onClick={() => setAll(true)}>show the whole table</button>
      )}
      {all && me >= 0 && <button className="gc-more" onClick={() => setAll(false)}>around {shortName(t.name)} only</button>}
      <p className="gc-chart-note">
        Built from the results in our database{table.asOf ? <>, latest <When iso={table.asOf} /></> : ''}. No
        points deductions and no conference split. &quot;Priced&quot; is the points the closing prices of
        those games expected; ± compares it with the points won in the same games.
      </p>
    </section>
  )
}

// ── recent matches ───────────────────────────────────────────────────────────

function Games({ games }: { games: TeamGame[] }) {
  const [all, setAll] = useState(false)
  const f = useOddsFormat()
  const shown = all ? games : games.slice(0, 10)
  return (
    <section className="gc-section">
      <h2 className="gc-h2">Recent matches</h2>
      <ul className="gcx-games">
        {shown.map((g, i) => (
          <li key={i}>
            <span className="gcx-games-date gc-mono"><When iso={g.date} /></span>
            <span className="gcx-games-venue">{g.venue === 'H' ? 'v' : '@'}</span>
            <span className="gcx-games-opp" title={g.league}>
              <Link href={teamHref(g.opponentId, g.opponent)} className="tm-link">{g.opponent}</Link>
            </span>
            <span className="gcx-games-ht gc-mono">{g.hf != null ? `${g.hf}-${g.ha}` : ''}</span>
            <span className="gcx-games-ft gc-mono">{g.gf}-{g.ga}</span>
            <Pill r={g.result} />
            <span className="gcx-games-tags">
              <i className={g.gf + g.ga > 2 ? 'on' : ''} title="Over 2.5">O</i>
              <i className={g.gf > 0 && g.ga > 0 ? 'on' : ''} title="Both teams scored">B</i>
            </span>
            <span className="gcx-games-odds gc-mono" title={g.oddsSource === 'pinnacle' ? 'Pinnacle closing odds on this team' : 'Market-average closing odds on this team'}>
              {g.odds ? oddsDec(g.odds, f) : ''}
            </span>
          </li>
        ))}
      </ul>
      {games.length > 10 && (
        <button className="gc-more" onClick={() => setAll((v) => !v)}>
          {all ? 'show fewer' : `show all ${games.length}`}
        </button>
      )}
      <p className="gc-chart-note">
        Half-time score, full-time score, <b>O</b> over 2.5, <b>B</b> both teams scored, and the
        closing odds on this club (Pinnacle, else the market average).
      </p>
    </section>
  )
}

// ── the page ─────────────────────────────────────────────────────────────────

export function TeamView({ t, upcoming }: { t: TeamPageData; upcoming: UpcomingGame[] | null }) {
  const row = t.table?.rows.find((r) => r.id === t.id) ?? null
  const pos = row && t.table ? t.table.rows.indexOf(row) + 1 : null
  const stale = t.lastPlayed && Date.now() - new Date(t.lastPlayed).getTime() > 21 * 864e5

  return (
    <div className="gc-main">
      <nav className="tm-crumb">
        <Link href="/teams">Teams</Link>
        {t.league && <> · <span>{t.league}</span></>}
      </nav>

      <header className="tm-hero">
        <h1>{t.name}</h1>
        <div className="gcx-hero-sub">
          {pos && t.table && (
            <span className="gcx-hero-pos">
              {pos} of {t.table.rows.length} · {row?.pts} pts
            </span>
          )}
          <span className="gcx-hero-form" title="Last five, latest first">
            {t.games.slice(0, 5).map((g, i) => <Pill key={i} r={g.result} />)}
          </span>
          {t.lastPlayed && (
            <span className="gcx-dim">last result in our data <When iso={t.lastPlayed} year /></span>
          )}
        </div>
        {stale && (
          <p className="gc-chart-note">
            ⚠️ No match for three weeks in our data. The latest results may not be loaded yet, or
            the season is over.
          </p>
        )}
      </header>

      <Upcoming games={upcoming} team={t.name} />
      <Market t={t} row={row} />

      {t.streaks.length > 0 && (
        <section className="gc-section">
          <h2 className="gc-h2">Runs worth knowing about</h2>
          <ul className="gcx-streaks">
            {t.streaks.map((s, i) => <StreakChip key={i} s={s} />)}
          </ul>
          <p className="gc-chart-note">
            Only runs a league-average side would put together 5% of the time or less are shown, and{' '}
            {t.checked} patterns were checked to find them, so expect about one to show up by chance.
          </p>
        </section>
      )}

      <Splits t={t} />
      <LeagueTable t={t} />
      <Games games={t.games} />
    </div>
  )
}
