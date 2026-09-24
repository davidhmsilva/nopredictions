'use client'

/** One game, as a card. The same card on the home page, where every sport is
 *  mixed on one grid, and at the top of each sport's own page.
 *
 *  Laid out the way a sportsbook lays out a moneyline: one line per outcome,
 *  the team's own name on the left and its price on the right. Where one app
 *  pays more after both apps' fees, the price is outlined and carries that
 *  app's logo — the job the boxed "P" and "K" used to do.
 *
 *  Soccer shows home / draw / away; the goals line stays in the table. A US
 *  game shows the two sides, away first, the way its schedule prints them,
 *  and the Over on its main total in the third line, where football has the
 *  draw. That line is Polymarket's alone, so it never carries a logo.
 */

import Link from 'next/link'
import { LiveState, money, odds, pickTitle } from './boardParts'
import { VenueLogo } from './VenueLogo'
import { columnsFor, outcomeLabel, type BoardRow } from '../lib/boardRow'
import { useOddsFormat } from '../lib/display'
import { VENUE_NAME, gradeOf, type OutcomeKey } from '../lib/venues'

/** What the total counts, for the tooltip. */
const TOTAL_UNIT: Record<string, string> = { mlb: 'runs', nhl: 'goals' }

function scoreFor(r: BoardRow, key: OutcomeKey): number | null {
  if (!r.score || (!r.live && !r.finished)) return null
  const homeIsLeft = r.sport === 'soccer'
  if (key === 'home') return homeIsLeft ? r.score.left : r.score.right
  if (key === 'away') return homeIsLeft ? r.score.right : r.score.left
  return null
}

export function GameCard({ r, showLeague = true }: { r: BoardRow; showLeague?: boolean }) {
  const f = useOddsFormat()
  const cols = columnsFor(r).filter((c) => c.key !== 'over25')

  return (
    <article className={`gm-card${r.live ? ' is-live' : ''}`}>
      <header className={`gm-top${showLeague ? '' : ' is-bare'}`}>
        {showLeague && <span className="gm-league">{r.competition ?? 'Soccer'}</span>}
        <LiveState r={r} />
      </header>

      <div className="gm-lines">
        {cols.map((c) => {
          const pick = r.best[c.key]
          const best = pick?.venue ?? null
          const score = scoreFor(r, c.key)
          return (
            <div key={c.key} className={`gm-line${c.key === 'draw' ? ' is-draw' : ''}`}>
              <span className="gm-name">{outcomeLabel(r, c.key)}</span>
              {score != null && <b className="gm-score np-num">{score}</b>}
              <span
                className={`gm-price${best ? ' is-best' : ''}`}
                title={pick && pick.ask != null ? pickTitle(pick, r.venues, c.key, f) : 'Not offered right now'}
              >
                {best && <VenueLogo venue={best} size={14} title="" />}
                <b className="np-num">{odds(pick?.ask ?? null, f)}</b>
              </span>
            </div>
          )
        })}
        {r.sport !== 'soccer' && <TotalLine r={r} />}
      </div>

      <footer className="gm-foot">
        <span className="gm-vol np-num">{money(r.volume)} traded</span>
        <span className="gm-venues">
          {r.venues.map((b) => (
            <a
              key={b.venue}
              href={b.url}
              target="_blank"
              rel="noopener noreferrer"
              title={`Open on ${VENUE_NAME[b.venue]}`}
            >
              <VenueLogo venue={b.venue} size={16} title="" />
            </a>
          ))}
        </span>
        {r.href && (
          // Stretched over the whole card, so the card opens the game. The
          // app logos sit above it and still open the app.
          <Link href={r.href} className="gm-go">
            Details <span aria-hidden="true">→</span>
          </Link>
        )}
      </footer>
    </article>
  )
}

/** The Over on a US game's main total, from Polymarket's own book. A quote
 *  with no real market behind it prints as a dash, the same as a price the
 *  venue does not offer. */
function TotalLine({ r }: { r: BoardRow }) {
  const f = useOddsFormat()
  const t = r.total
  const q = t && gradeOf([t.over]) !== 'none' ? t.over : null
  const unit = TOTAL_UNIT[r.sport] ?? 'points'
  return (
    <div className="gm-line is-total">
      <span className="gm-name">{t ? `Over ${t.line}` : 'Over / under'}</span>
      <span
        className="gm-price"
        title={
          t && q
            ? `Over ${t.line} total ${unit} on Polymarket. Kalshi's totals are not compared here yet.`
            : 'Not offered right now'
        }
      >
        <b className="np-num">{odds(q?.ask ?? null, f)}</b>
      </span>
    </div>
  )
}

/** Placeholder while the boards load, the same size as a card so nothing
 *  jumps when they land. */
export function GameCardSkeleton() {
  return (
    <div className="gm-card is-skeleton" aria-hidden="true">
      <span className="gm-sk gm-sk-s" />
      <span className="gm-sk" />
      <span className="gm-sk" />
      <span className="gm-sk" />
      <span className="gm-sk gm-sk-s" />
    </div>
  )
}
