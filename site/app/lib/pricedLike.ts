// "Matches priced like this one" — what every other market did in the matches
// the sharpest book priced the same way.
//
// Built by agent/priced_like_table.py: ~48k matches with a Pinnacle closing
// Over 2.5, ~101k with a closing 1X2, both proportionally de-vigged, results
// from our own `matches`. No stats site can make this comparison — they have
// results, not results joined to closing prices.
//
// ⚠️ What a gap here is NOT: an edge. The table knows the price of one market
// and nothing about who is playing; Polymarket's price for the other markets
// knows both. A gap says the board is pricing this fixture differently from an
// average match at the same line — which is often exactly right. The page says
// so beside every number.

import TABLE from './priced_like.json'
import {
  matchTotalLine,
  takerFeePp,
  teamScore,
  type Headline,
  type MarketGroup,
} from './gamecenter'
import { shortTeam } from './teamname'

const MIN_SIDE_SCORE = 0.6

export interface PricedLikeLine {
  key: string
  label: string
  rate: number | null
  n: number
  /** Polymarket's ask for the same outcome — only before kick-off. */
  pmAsk: number | null
  pmIsMid: boolean
  /** history − ask, after Polymarket's taker fee. A gap, never an edge. */
  gapPp: number | null
}

export interface PricedLikeBlock {
  /** The de-vigged probability the bucket was chosen on. */
  prob: number
  lo: number
  hi: number
  n: number
  lines: PricedLikeLine[]
}

export interface PricedLike {
  totals: (PricedLikeBlock & { avgGoals: number | null; avgHtGoals: number | null }) | null
  result: PricedLikeBlock | null
  /** Polymarket's prices are only comparable to pre-match rates before kick-off. */
  comparable: boolean
  source: string
}

function bucket<T extends { lo: number; hi: number }>(rows: T[], p: number): T | null {
  return rows.find((r) => p >= r.lo && p < r.hi) ?? null
}

function askOf(g: MarketGroup | undefined, side: RegExp): { ask: number; isMid: boolean } | null {
  const o = g?.outcomes.find((x) => side.test(x.name))
  if (!o) return null
  if (o.book?.ask != null) return { ask: o.book.ask, isMid: false }
  return o.price != null ? { ask: o.price, isMid: true } : null
}

const tail = (q: string) => q.split(':').pop()?.trim() ?? ''

function line(
  key: string,
  label: string,
  rate: number | null,
  n: number,
  pm: { ask: number; isMid: boolean } | null,
  comparable: boolean
): PricedLikeLine {
  const usable = comparable && pm && pm.ask > 0.01 && pm.ask < 0.99 ? pm : null
  return {
    key,
    label,
    rate,
    n,
    pmAsk: usable?.ask ?? null,
    pmIsMid: usable?.isMid ?? false,
    gapPp:
      usable && rate != null ? (rate - usable.ask) * 100 - takerFeePp(usable.ask) : null,
  }
}

/** "X leading at halftime?" for one side, resolved by the alias-aware scorer —
 *  never by title order, which inverts the bet when it is wrong. */
function leadingAtHalf(groups: MarketGroup[], team: string): MarketGroup | undefined {
  let best: { g: MarketGroup; s: number } | null = null
  for (const g of groups) {
    const m = g.question.match(/^(?:will\s+)?(.+?)\s+(?:be\s+)?lead(?:ing)?\s+at\s+half-?\s?time/i)
    if (!m) continue
    const s = teamScore(team, m[1])
    if (s >= MIN_SIDE_SCORE && (!best || s > best.s)) best = { g, s }
  }
  return best?.g
}

export function buildPricedLike(opts: {
  groups: MarketGroup[]
  headlines: Headline[]
  home: string
  away: string
  preOver25: number | null
  started: boolean
}): PricedLike {
  const { groups, headlines, home, away, preOver25, started } = opts
  const comparable = !started

  let totals: PricedLike['totals'] = null
  if (preOver25 != null) {
    const row = bucket(TABLE.totals, preOver25)
    if (row) {
      const total = (l: number) => groups.find((g) => matchTotalLine(g.question) === l)
      const btts = groups.find(
        (g) => g.group === 'Both teams to score' && !/half|1st|2nd/i.test(g.question)
      )
      const half = (l: string) =>
        groups.find(
          (g) =>
            g.group === 'Halftime' &&
            new RegExp(`^(1st|first) half\\s+o\\/u\\s*${l.replace('.', '\\.')}$`, 'i').test(tail(g.question))
        )
      totals = {
        prob: preOver25,
        lo: row.lo,
        hi: row.hi,
        n: row.n,
        avgGoals: row.avg_goals,
        avgHtGoals: row.avg_ht_goals,
        lines: [
          line('o15', 'Over 1.5 goals', row.o15, row.n, askOf(total(1.5), /^over$/i), comparable),
          line('o25', 'Over 2.5 goals', row.o25, row.n, askOf(total(2.5), /^over$/i), comparable),
          line('o35', 'Over 3.5 goals', row.o35, row.n, askOf(total(3.5), /^over$/i), comparable),
          line('btts', 'Both teams score', row.btts, row.n, askOf(btts, /^yes$/i), comparable),
          line('ht_o05', 'Goal in the 1st half', row.ht_o05, row.n_ht, askOf(half('0.5'), /^over$/i), comparable),
          line('ht_o15', '1st half over 1.5', row.ht_o15, row.n_ht, askOf(half('1.5'), /^over$/i), comparable),
          line('sh_o05', 'Goal in the 2nd half', row.sh_o05, row.n_ht, null, comparable),
        ],
      }
    }
  }

  // The 1X2 bucket needs a PRE-MATCH price. Once the match is live the current
  // quote has absorbed the score, so the favourite table is only built before
  // kick-off.
  let result: PricedLike['result'] = null
  if (!started) {
    // The same function that labelled the headlines — this lookup is BY label.
    const shortName = shortTeam
    const h = headlines.find((x) => x.label === shortName(home))
    const d = headlines.find((x) => x.label === 'Draw')
    const a = headlines.find((x) => x.label === shortName(away))
    if (h?.prob && d?.prob && a?.prob) {
      const ph = h.prob / (h.prob + d.prob + a.prob)
      const row = bucket(TABLE.home, ph)
      if (row) {
        const pmOf = (x: Headline) => ({ ask: x.prob as number, isMid: x.isMid })
        result = {
          prob: ph,
          lo: row.lo,
          hi: row.hi,
          n: row.n,
          lines: [
            line('home', `${shortName(home)} win`, row.home, row.n, pmOf(h), comparable),
            line('draw', 'Draw', row.draw, row.n, pmOf(d), comparable),
            line('away', `${shortName(away)} win`, row.away, row.n, pmOf(a), comparable),
            line('ht_home', `${shortName(home)} ahead at half time`, row.ht_home, row.n_ht,
              askOf(leadingAtHalf(groups, home), /^yes$/i), comparable),
            line('ht_draw', 'Level at half time', row.ht_draw, row.n_ht, null, comparable),
            line('ht_away', `${shortName(away)} ahead at half time`, row.ht_away, row.n_ht,
              askOf(leadingAtHalf(groups, away), /^yes$/i), comparable),
          ],
        }
      }
    }
  }

  return {
    totals,
    result,
    comparable,
    source: `${TABLE.meta.matches_totals.toLocaleString('en')} matches with a Pinnacle closing total, ${TABLE.meta.matches_home.toLocaleString('en')} with a closing 1X2`,
  }
}
