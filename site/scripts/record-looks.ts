/** The forward record of the Game Center's CHEAP cards (db/081).
 *
 *  Every minute: every live fixture on the Scout board gets the Game Center's
 *  own payload, built by the page's own route handler, and every card it would
 *  show as CHEAP on a real book is written to `look_observations`, once per
 *  (fixture, card, score). The route is imported, not copied, so the record
 *  and the page cannot disagree about what a reader was shown.
 *
 *  `--settle` pays each row on Polymarket's resolution of the token (the CLOB
 *  `winner` flag). Never the score: the market is the bet.
 *
 *    NP_PUBLISHER=1 DATABASE_URL=... npx tsx scripts/record-looks.ts           # forever, 60s
 *    ... --once        one cycle, then exit
 *    ... --dry-run     one cycle, print, write nothing
 *    ... --settle      settle what has resolved, then exit
 *    ... --report      what has been recorded
 *
 *  With BOARDS_DIR set the Scout board is read from the publisher's file
 *  instead of swept again.
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

if (process.env.NP_PUBLISHER !== '1') {
  console.error('set NP_PUBLISHER=1: the board modules need it to run outside Next')
  process.exit(2)
}

const CYCLE_MS = Number(process.env.LOOKS_CYCLE_MS || 60_000)
const ONCE = process.argv.includes('--once')
const DRY = process.argv.includes('--dry-run')
// The tables behind the cards cover 10'-88'. Outside that no card is built,
// so the fixture costs nothing.
const MIN_MINUTE = 10
const MAX_MINUTE = 89
const CONCURRENCY = 4
// A market settles minutes to hours after the whistle; asking before that is
// a wasted request.
const SETTLE_AFTER_MIN = 90

type Fixture = { slug: string; live: boolean; minute: number | null; finished: boolean }

function log(msg: string) {
  console.log(`${new Date().toISOString()} [record-looks] ${msg}`)
}

async function liveFixtures(): Promise<Fixture[]> {
  const dir = process.env.BOARDS_DIR
  let fixtures: Fixture[]
  if (dir) {
    fixtures = JSON.parse(await readFile(join(dir, 'scout.json'), 'utf8')).fixtures
  } else {
    const { sweep } = await import('../app/lib/scoutCache')
    fixtures = (await sweep()).fixtures
  }
  return fixtures.filter(
    (f) => f.live && !f.finished && f.minute != null && f.minute >= MIN_MINUTE && f.minute <= MAX_MINUTE
  )
}

async function pool<T, R>(items: T[], n: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = []
  let i = 0
  await Promise.all(
    Array.from({ length: Math.min(n, items.length) }, async () => {
      while (i < items.length) {
        const k = i++
        out[k] = await fn(items[k])
      }
    })
  )
  return out
}

async function cycle() {
  const { GET } = await import('../app/api/game/route')
  const { tradeable } = await import('../app/lib/looks')
  const { getSql } = await import('../app/lib/db')
  type GameData = import('../app/lib/gamecenter').GameData

  const fixtures = await liveFixtures()
  let built = 0
  let failed = 0
  const rows: Record<string, unknown>[] = []

  await pool(fixtures, CONCURRENCY, async (f) => {
    try {
      const res = await GET(new Request(`http://local/api/game?slug=${encodeURIComponent(f.slug)}`))
      if (!res.ok) {
        failed++
        return
      }
      const g = (await res.json()) as GameData
      built++
      if (!g.live || !g.looks?.length) return
      const goals = g.live.homeGoals + g.live.awayGoals
      for (const l of g.looks) {
        if (l.call !== 'back' || !tradeable(l) || !l.tokenId) continue
        const group = g.groups.find((x) => x.outcomes.some((o) => o.tokenId === l.tokenId))
        rows.push({
          slug: g.slug,
          event_title: g.title,
          competition: g.competition,
          minute: g.live.minute,
          goals,
          clock_source: g.live.clockSource,
          look_id: l.id,
          side: l.side,
          market: l.market,
          market_slug: group?.slug ?? null,
          token_id: l.tokenId,
          tier: l.tier,
          ask: l.prob,
          odds: l.odds,
          fair_prob: l.fairProb,
          fair_raw_prob: l.fairRawProb,
          calibrated: l.calibrated,
          imported: l.imported,
          n: l.n,
          edge_pp: l.edgePp,
          fee_pp: l.feePp,
          spread_pp: l.spreadPp,
          depth_usd: l.depthUsd,
        })
      }
    } catch (e) {
      failed++
      log(`${f.slug}: ${e instanceof Error ? e.message : e}`)
    }
  })

  let written = 0
  if (rows.length && !DRY) {
    const sql = getSql()
    const res = await sql`
      insert into look_observations ${sql(rows)}
      on conflict (slug, look_id, goals) do nothing
      returning id`
    written = res.length
  }
  if (DRY) for (const r of rows) log(`would record ${r.slug} ${r.minute}' ${r.side} @ ${Number(r.odds).toFixed(2)} edge ${Number(r.edge_pp).toFixed(1)}pp`)
  log(`live ${fixtures.length} · built ${built} · failed ${failed} · cheap ${rows.length} · new ${written}`)
}

/** Polymarket's verdict on one token: true/false once resolved, null if not yet. */
async function verdict(marketSlug: string | null, tokenId: string): Promise<boolean | null> {
  let conditionId: string | null = null
  if (marketSlug) {
    const r = await fetch(`https://gamma-api.polymarket.com/markets?slug=${encodeURIComponent(marketSlug)}&closed=true`)
    const m = r.ok ? ((await r.json()) as Array<{ conditionId?: string }>)[0] : undefined
    conditionId = m?.conditionId ?? null
  }
  if (!conditionId) {
    const r = await fetch(`https://gamma-api.polymarket.com/markets?clob_token_ids=${tokenId}&closed=true`)
    const m = r.ok ? ((await r.json()) as Array<{ conditionId?: string }>)[0] : undefined
    conditionId = m?.conditionId ?? null
  }
  if (!conditionId) return null
  const r = await fetch(`https://clob.polymarket.com/markets/${conditionId}`)
  if (!r.ok) return null
  const m = (await r.json()) as { closed?: boolean; tokens?: Array<{ token_id: string; winner: boolean }> }
  if (!m.closed || !m.tokens?.length) return null
  // A 50-50 or an unresolved close pays nobody a clean winner: not a result.
  if (!m.tokens.some((t) => t.winner)) return null
  const t = m.tokens.find((x) => x.token_id === tokenId)
  return t ? t.winner : null
}

async function settle() {
  const { getSql } = await import('../app/lib/db')
  const sql = getSql()
  const open = await sql<{ id: number; market_slug: string | null; token_id: string }[]>`
    select id, market_slug, token_id from look_observations
     where settled_at is null and observed_at < now() - make_interval(mins => ${SETTLE_AFTER_MIN})
     order by observed_at limit 500`
  let done = 0
  for (const row of open) {
    try {
      const won = await verdict(row.market_slug, row.token_id)
      if (won == null) continue
      await sql`update look_observations
                   set won = ${won}, settled_at = now(), settle_source = 'pm'
                 where id = ${row.id}`
      done++
    } catch (e) {
      log(`settle ${row.id}: ${e instanceof Error ? e.message : e}`)
    }
  }
  log(`open ${open.length} · settled ${done}`)
}

async function report() {
  const { getSql } = await import('../app/lib/db')
  const sql = getSql()
  const [all] = await sql`
    select count(*)::int n, count(won)::int settled, count(*) filter (where won)::int won,
           avg(ask) filter (where won is not null) avg_ask,
           avg(fair_prob) filter (where won is not null) avg_fair,
           sum(case when won then 1 / ask - 1 - fee_pp / 100 / ask else -1 - fee_pp / 100 / ask end)
             filter (where won is not null) net_units,
           count(distinct slug)::int fixtures
      from look_observations`
  log(`recorded ${all.n} on ${all.fixtures} fixtures · settled ${all.settled} · won ${all.won}`)
  if (all.settled) {
    log(`hit ${(all.won / all.settled).toFixed(3)} vs ask ${Number(all.avg_ask).toFixed(3)} vs our fair ${Number(all.avg_fair).toFixed(3)}`)
    log(`1u flat, net of the taker fee: ${Number(all.net_units).toFixed(2)}u (${((100 * Number(all.net_units)) / all.settled).toFixed(1)}%)`)
    log(`not a result below a few hundred settled rows — read hit vs ask first`)
  }
}

async function main() {
  if (process.argv.includes('--settle')) return settle()
  if (process.argv.includes('--report')) return report()
  for (;;) {
    const t0 = Date.now()
    try {
      await cycle()
    } catch (e) {
      log(`cycle failed: ${e instanceof Error ? e.message : e}`)
    }
    if (ONCE || DRY) return
    await new Promise((r) => setTimeout(r, Math.max(5_000, CYCLE_MS - (Date.now() - t0))))
  }
}

main()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error(e)
    process.exit(1)
  })
