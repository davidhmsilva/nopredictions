/**
 * Polymarket wallet analyser — the site's port of agent/wallet_analyzer.py.
 *
 * ⚠️ THIS FILE IS A PORT, NOT THE SOURCE OF TRUTH. The Python is where the
 * method lives; this exists because the analysis has to run inside a serverless
 * request. Two implementations of a FIFO reconstruction WILL drift, and a page
 * that quietly disagrees with the research it came from is worse than no page —
 * so keep them in step and check with:
 *
 *     python agent/wallet_analyzer.py <addr> --verify-site https://nopredictions.com
 *
 * The traps are documented in the Python. The short version:
 *   · `/activity?offset=` refuses past 5000 — page by time cursor, never offset.
 *   · `price` on a fill is the price BEFORE the fee; usdcSize is the cash, fee
 *     included. P&L uses usdcSize / size, and the gap between the two is the fee.
 *   · Polymarket's leaderboard profit is GROSS of fees and leaves rebates out —
 *     reconcile against P&L + fees, or every fee-paying wallet looks broken.
 *   · REDEEM rows carry no `asset` — map (conditionId, outcomeIndex) → token.
 *   · Shares sold that were never bought came from neg-risk conversions, which
 *     the feed does not publish. Book them FLAT, never free.
 *   · Gamma returns NOTHING for a settled market unless closed=true is passed.
 */

const DATA_API = 'https://data-api.polymarket.com'
const GAMMA_API = 'https://gamma-api.polymarket.com'
const LB_API = 'https://lb-api.polymarket.com'

const PAGE = 500
const GAMMA_BATCH = 100 // 200 condition_ids → HTTP 422
const MAX_OFFSET = 5000
const WINDOWS = 8 // parallel slices of the wallet's lifetime
// A market maker can run to hundreds of thousands of fills, and a serverless
// request has neither the time nor the memory for that. Both limits produce a
// LABELLED partial — `complete` goes false and the narrative leads with it —
// never a silent truncation and never a 504 with nothing in it.
const MAX_ROWS = 250_000
const BUDGET_MS = 45_000

const WIN_IN_MATCH: [number, number] = [0, 110]
const WIN_WHISTLE: [number, number] = [110, 130]

const ENTRY_BANDS: [number, number][] = [
  [0.0, 0.05], [0.05, 0.1], [0.1, 0.2], [0.2, 0.4], [0.4, 0.6],
  [0.6, 0.8], [0.8, 0.9], [0.9, 0.95], [0.95, 1.01],
]

const MOVE_BANDS: [string, number, number][] = [
  ['loss < -0.05', -9, -0.05],
  ['flat -0.05..+0.02', -0.05, 0.02],
  ['+0.02..+0.10', 0.02, 0.1],
  ['+0.10..+0.30', 0.1, 0.3],
  ['> +0.30', 0.3, 9],
]

/** The 110-130′ whistle window is a FOOTBALL fact. 110 minutes after a tennis
 *  match starts is the middle of it, not the end, and a wallet that trades ATP
 *  would be handed a "post-whistle settlement buyer" verdict off nothing. */
const NON_FOOTBALL = new Set(['NBA', 'NFL', 'MLB', 'NHL', 'ATP', 'WTA', 'ITF', 'CS', 'LOL',
  'DOTA', 'VAL', 'UFC', 'F1', 'NCAAF', 'NCAAB', 'GOLF', 'PGA'])

/** Polymarket's event tickers lead with a competition code and nothing expands
 *  them. Unknown codes stay as themselves rather than being pooled into "other". */
const COMPETITIONS: Record<string, string> = {
  fifwc: 'FIFA World Cup', fif: 'Internationals', wcq: 'WC qualifiers',
  epl: 'Premier League', esp: 'La Liga', ita: 'Serie A', bun: 'Bundesliga',
  fra: 'Ligue 1', ned: 'Eredivisie', por: 'Primeira Liga', efl: 'Championship',
  ucl: 'Champions League', uel: 'Europa League', uecl: 'Conference League',
  bra: 'Brasileirão', bra2: 'Brasileirão B', cdb: 'Copa do Brasil',
  arg: 'Argentina Primera', mls: 'MLS', lmx: 'Liga MX', col: 'Conference League',
  chi: 'Chile', lib: 'Libertadores', sud: 'Sudamericana',
  lc: 'Leagues Cup', lec: 'Leagues Cup', col1: 'Colombia Primera A',
  csl: 'Chinese Super League', egy: 'Egypt', tur: 'Süper Lig',
  sco: 'Scottish Premiership', bel: 'Belgian Pro League', nba: 'NBA',
  nfl: 'NFL', mlb: 'MLB', nhl: 'NHL',
}

// ─── types ──────────────────────────────────────────────────────────────────

const FOOTBALL = new Set(Object.values(COMPETITIONS).filter((v) => !NON_FOOTBALL.has(v)))

export interface ActivityRow {
  timestamp: number
  type: string
  side?: string
  asset?: string
  conditionId?: string
  eventSlug?: string
  slug?: string
  title?: string
  outcome?: string
  outcomeIndex?: number
  size?: number
  usdcSize?: number
  price?: number
  transactionHash?: string
  name?: string
  pseudonym?: string
  profileImage?: string
}

export interface GammaMarket {
  conditionId: string
  clobTokenIds?: string
  outcomes?: string
  outcomePrices?: string
  closed?: boolean
  gameStartTime?: string
  startDate?: string
  sportsMarketType?: string
  events?: { ticker?: string; title?: string }[]
}

interface Lot {
  asset: string
  conditionId: string
  eventSlug: string
  title: string
  outcome: string
  shares: number
  entryPrice: number
  entryTs: number
  exitPrice: number
  exitTs: number | null
  exitKind: 'sell' | 'redeem' | 'merge' | 'resolved' | 'open' | 'unmatched'
  entryMinute: number | null
}

const cost = (l: Lot) => l.shares * l.entryPrice
const proceeds = (l: Lot) => l.shares * l.exitPrice
const lotPnl = (l: Lot) => proceeds(l) - cost(l)
const holdMin = (l: Lot) => (l.exitTs === null ? null : Math.max(0, l.exitTs - l.entryTs) / 60)

export interface WalletProfile {
  wallet: string
  name: string
  pseudonym: string
  profile_image: string
  generated_at: string
  coverage: Record<string, any>
  totals: Record<string, any>
  reconciliation: Record<string, any>
  exposure: Record<string, any>
  hold: Record<string, any>
  exits: Record<string, any>
  timing: Record<string, any>
  entry_bands: any[]
  moves: any[]
  months: any[]
  days: Record<string, any>
  universe: any[]
  market_types: any[]
  concentration: Record<string, any>
  sweeps: Record<string, any>
  bootstrap: Record<string, any>
  sport: Record<string, number>
  trust: 'ok' | 'partial' | 'unreconciled'
  archetypes: { key: string; label: string; confidence: string; evidence: string[] }[]
  narrative: { headline: string; sections: { title: string; paragraphs: string[] }[] }
}

// ─── deterministic PRNG (must match the Python, seed for seed) ──────────────
// Pinned in agent/tests/test_wallet_analyzer.py: seed 42 →
// 0.60110375192, 0.448290558998, 0.85246579349, 0.669734041439, 0.174813898746

export function mulberry32(seed: number) {
  let s = seed >>> 0
  return function next(): number {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0
    t = (t ^ (t + (Math.imul(t ^ (t >>> 7), t | 61) >>> 0))) >>> 0
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ─── fetch layer ────────────────────────────────────────────────────────────

async function getJson(url: string, tries = 4): Promise<any> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(30000), cache: 'no-store' })
      if (r.ok) return await r.json()
      if (![429, 502, 503, 504].includes(r.status)) return null
    } catch {
      /* retry */
    }
    await new Promise((res) => setTimeout(res, 400 * (i + 1)))
  }
  return null
}

const rowKey = (r: ActivityRow) =>
  [r.timestamp, r.transactionHash, r.asset, r.type, r.side, r.size, r.usdcSize, r.price,
    r.conditionId, r.outcomeIndex].join('|')

/** One descending cursor walk over [start, end]. */
async function walk(
  wallet: string, start: number | null, end: number | null, into: Map<string, ActivityRow>,
  deadline = Infinity,
): Promise<boolean> {
  let cursor = end
  for (let page = 0; page < MAX_ROWS / PAGE + 1; page++) {
    if (Date.now() > deadline || into.size >= MAX_ROWS) return false
    const qs = new URLSearchParams({ user: wallet, limit: String(PAGE) })
    if (cursor !== null) qs.set('end', String(cursor))
    if (start !== null) qs.set('start', String(start))
    const batch: ActivityRow[] = (await getJson(`${DATA_API}/activity?${qs}`)) || []
    if (!batch.length) return true
    for (const row of batch) {
      const k = rowKey(row)
      if (!into.has(k)) into.set(k, row)
    }
    const stamps = batch.map((b) => b.timestamp)
    const lo = minOf(stamps)
    const hi = maxOf(stamps)
    if (batch.length < PAGE) return true
    if (lo === hi) {
      // A single second filled the page. Drain it by offset or the cursor can
      // never step past it.
      for (let off = PAGE; off <= MAX_OFFSET; off += PAGE) {
        const extra: ActivityRow[] =
          (await getJson(`${DATA_API}/activity?user=${wallet}&limit=${PAGE}&start=${lo}&end=${lo}&offset=${off}`)) || []
        for (const row of extra) {
          const k = rowKey(row)
          if (!into.has(k)) into.set(k, row)
        }
        if (extra.length < PAGE) break
      }
      cursor = lo - 1
    } else {
      cursor = lo
    }
    if (start !== null && cursor < start) return true
  }
  return false // stopped on the page guard, not on the end of the history
}

/** Every activity row for `wallet`.
 *
 *  The lifetime is sliced into disjoint windows walked in parallel — a 16k-row
 *  wallet is 33 serial pages, which is most of a serverless request's budget.
 *  Windows are half-open so no row can be counted twice, and the row count is
 *  one of the fields `--verify-site` pins against the Python. */
export async function fetchActivity(
  wallet: string, since: number | null = null,
): Promise<{ rows: ActivityRow[]; complete: boolean }> {
  const seen = new Map<string, ActivityRow>()
  const now = Math.floor(Date.now() / 1000) + 60

  const oldest: ActivityRow[] =
    (await getJson(`${DATA_API}/activity?user=${wallet}&limit=1&sortDirection=ASC`)) || []
  if (!oldest.length) return { rows: [], complete: true }
  const first = Math.max(oldest[0].timestamp - 1, since ?? 0)

  let complete = true
  const span = now - first
  const deadline = Date.now() + BUDGET_MS
  if (span < 3600 * 6) {
    complete = await walk(wallet, since, null, seen, deadline)
  } else {
    const edges: number[] = []
    for (let i = 0; i <= WINDOWS; i++) edges.push(Math.floor(first + (span * i) / WINDOWS))
    const results = await Promise.all(
      edges.slice(0, -1).map((lo, i) => {
        const hi = i === WINDOWS - 1 ? null : edges[i + 1] - 1
        return walk(wallet, lo, hi, seen, deadline)
      }),
    )
    complete = results.every(Boolean)
  }
  const rows = Array.from(seen.values()).sort(
    (a, b) => a.timestamp - b.timestamp || (a.transactionHash || '').localeCompare(b.transactionHash || ''),
  )
  return { rows, complete }
}

/** ⚠️ Both closed states have to be swept — Gamma's default returns nothing at
 *  all for a settled market, which reads as "no metadata" rather than an error. */
export async function fetchMarkets(conditionIds: string[]): Promise<Record<string, GammaMarket>> {
  const cids = Array.from(new Set(conditionIds.filter(Boolean))).sort()
  const out: Record<string, GammaMarket> = {}
  const jobs: Promise<void>[] = []
  for (let i = 0; i < cids.length; i += GAMMA_BATCH) {
    const qs = cids.slice(i, i + GAMMA_BATCH).map((c) => `condition_ids=${c}`).join('&')
    for (const closed of ['true', 'false']) {
      jobs.push(
        getJson(`${GAMMA_API}/markets?${qs}&closed=${closed}&limit=500`).then((data) => {
          for (const m of (data as GammaMarket[]) || []) out[m.conditionId] = m
        }),
      )
    }
  }
  await Promise.all(jobs)
  return out
}

export async function fetchLbProfit(wallet: string): Promise<number | null> {
  const d = await getJson(`${LB_API}/profit?window=all&limit=1&address=${wallet}`)
  return Array.isArray(d) && d.length ? Number(d[0].amount) : null
}

/** How far PM's leaderboard may sit from the gross reconstruction and still
 *  agree — king1605 sits 0.35% off after fees, for a reason not yet found. */
const RECON_TOL_PCT = 0.01
const SOCCER_TAG = '100350'

/** Gamma's league list keyed by the code event tickers lead with — names every
 *  league and tags the soccer ones. Series ids are NOT used: they change by
 *  season. A failure returns {} and the hand-kept table answers alone. */
export async function fetchSports(): Promise<Sports> {
  const out: Sports = {}
  try {
    const d = await getJson(`${GAMMA_API}/sports`)
    for (const row of Array.isArray(d) ? d : []) {
      const code = String(row.sport || '').toLowerCase()
      if (code) {
        out[code] = {
          name: row.name || code.toUpperCase(),
          football: String(row.tags || '').split(',').includes(SOCCER_TAG),
        }
      }
    }
  } catch {
    // the hand-kept table still answers for the codes it knows
  }
  return out
}

export async function fetchCurrentValue(wallet: string): Promise<number | null> {
  const d = await getJson(`${DATA_API}/value?user=${wallet}`)
  return Array.isArray(d) && d.length ? Number(d[0].value) : null
}

// ─── market metadata helpers ────────────────────────────────────────────────

function jsonList(v: any): any[] {
  if (Array.isArray(v)) return v
  if (typeof v === 'string' && v.trim()) {
    try {
      return JSON.parse(v)
    } catch {
      return []
    }
  }
  return []
}

function kickoffTs(m?: GammaMarket): number | null {
  const raw = m?.gameStartTime || m?.startDate || ''
  if (!raw) return null
  let txt = String(raw).trim().replace(' ', 'T')
  if (txt.endsWith('+00')) txt = txt.slice(0, -3) + 'Z'
  const t = Date.parse(txt.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(txt) ? txt : txt + 'Z')
  return Number.isNaN(t) ? null : Math.floor(t / 1000)
}

function resolution(m?: GammaMarket): Record<string, number> | null {
  if (!m || !m.closed) return null
  const prices = jsonList(m.outcomePrices).map(Number).filter((p) => !Number.isNaN(p))
  const outcomes = jsonList(m.outcomes).map(String)
  if (!prices.length || prices.length !== outcomes.length) return null
  const out: Record<string, number> = {}
  const settled = prices.some((p) => p > 0.99)
  outcomes.forEach((o, i) => (out[o] = settled ? (prices[i] > 0.99 ? 1 : 0) : prices[i]))
  return out
}

function mark(m: GammaMarket | undefined, outcome: string): number | null {
  const prices = jsonList(m?.outcomePrices).map(Number)
  const outcomes = jsonList(m?.outcomes).map(String)
  const i = outcomes.indexOf(outcome)
  return i >= 0 && !Number.isNaN(prices[i]) ? prices[i] : null
}

/** true / false / null — "we do not know" is a distinct answer, and the
 *  football-specific windows are withheld for it rather than assumed. */
function isFootball(label: string): boolean | null {
  if (NON_FOOTBALL.has(label)) return false
  if (FOOTBALL.has(label)) return true
  return null
}

export type Sports = Record<string, { name: string; football: boolean }>

const tickerHead = (m?: GammaMarket) => String(m?.events?.[0]?.ticker || '').split('-')[0].toLowerCase()

function competition(m?: GammaMarket, sports?: Sports): string {
  const head = tickerHead(m)
  if (!head) return 'unknown'
  if (COMPETITIONS[head]) return COMPETITIONS[head]
  if (sports?.[head]) return sports[head].name
  return head.toUpperCase()
}

/** Gamma's own league list decides first; the hand-kept table only answers
 *  for codes that list does not carry. */
function football(m?: GammaMarket, sports?: Sports): boolean | null {
  const head = tickerHead(m)
  if (sports?.[head]) return sports[head].football
  return isFootball(competition(m, sports))
}

// ─── the reconstruction ─────────────────────────────────────────────────────

interface Flows {
  buys: number; sells: number; redeems: number; merges: number; mergeProceeds: number
  deployed: number; sellProceeds: number; redeemProceeds: number; openValue: number
  rebates: number; rewards: number; fees: number; unhandled: Record<string, number>
}

/** What the first `shares` in the queue cost, without consuming them. */
function peekCost(q: [number, number, number][], shares: number): number {
  let left = shares
  let total = 0
  for (const [lotShares, lotPrice] of q) {
    const take = Math.min(left, lotShares)
    total += take * lotPrice
    left -= take
    if (left <= 1e-9) break
  }
  return total
}

function consume(
  q: [number, number, number][], shares: number, price: number, ts: number,
  kind: Lot['exitKind'], asset: string, meta: Record<string, any>, lots: Lot[],
) {
  let left = shares
  const mm = meta[asset] || {}
  const base = {
    asset, conditionId: mm.conditionId || '', eventSlug: mm.eventSlug || '',
    title: mm.title || '', outcome: mm.outcome || '', entryMinute: null,
  }
  while (left > 1e-9 && q.length) {
    const [lotShares, lotPrice, lotTs] = q[0]
    const take = Math.min(left, lotShares)
    lots.push({ ...base, shares: take, entryPrice: lotPrice, entryTs: lotTs, exitPrice: price, exitTs: ts, exitKind: kind })
    left -= take
    if (take >= lotShares - 1e-9) q.shift()
    else q[0][0] = lotShares - take
  }
  if (left > 1e-6) {
    // Sold shares we never saw bought — neg-risk conversions, which the feed
    // does not publish. Booked FLAT: entry = exit, so they add no profit.
    // Booked free they would be pure invented P&L, and that is an error that
    // can only ever run one way.
    lots.push({ ...base, shares: left, entryPrice: price, entryTs: ts, exitPrice: price, exitTs: ts, exitKind: 'unmatched' })
  }
}

export function buildLots(rows: ActivityRow[], markets: Record<string, GammaMarket>): { lots: Lot[]; flows: Flows } {
  const queues: Record<string, [number, number, number][]> = {}
  const lots: Lot[] = []
  const meta: Record<string, any> = {}
  const unhandled: Record<string, number> = {}
  const flows: Flows = {
    buys: 0, sells: 0, redeems: 0, merges: 0, mergeProceeds: 0, deployed: 0, sellProceeds: 0,
    redeemProceeds: 0, openValue: 0, rebates: 0, rewards: 0, fees: 0, unhandled,
  }

  const tokenOf: Record<string, string> = {}
  for (const [cid, m] of Object.entries(markets)) {
    jsonList(m.clobTokenIds).forEach((tok, idx) => (tokenOf[`${cid}|${idx}`] = String(tok)))
  }
  const remember = (asset: string, r: ActivityRow) => {
    if (!meta[asset]) {
      meta[asset] = {
        conditionId: r.conditionId || '', eventSlug: r.eventSlug || '',
        title: r.title || '', outcome: r.outcome || '',
      }
    }
  }

  for (const r of rows) {
    const usd = Number(r.usdcSize || 0)
    const size = Number(r.size || 0)
    const ts = Number(r.timestamp || 0)

    if (r.type === 'TRADE') {
      const asset = r.asset || ''
      if (!asset || size <= 0) continue
      remember(asset, r)
      // ⚠️ NOT r.price — that is the price BEFORE the fee. The money moved is
      // the price, and the gap between the two is the fee (see the Python).
      const price = usd / size
      const px = Number(r.price || 0)
      if (px > 0) flows.fees += r.side === 'BUY' ? usd - px * size : px * size - usd
      if (r.side === 'BUY') {
        flows.buys++
        flows.deployed += usd
        ;(queues[asset] = queues[asset] || []).push([size, price, ts])
      } else {
        flows.sells++
        flows.sellProceeds += usd
        consume((queues[asset] = queues[asset] || []), size, price, ts, 'sell', asset, meta, lots)
      }
    } else if (r.type === 'REDEEM') {
      const cid = r.conditionId || ''
      let asset = r.outcomeIndex === undefined ? undefined : tokenOf[`${cid}|${r.outcomeIndex}`]
      if (!asset) {
        const cands = Object.keys(meta).filter((a) => meta[a].conditionId === cid)
        asset = cands.length === 1 ? cands[0] : undefined
      }
      if (!asset) {
        unhandled['REDEEM (unmapped)'] = (unhandled['REDEEM (unmapped)'] || 0) + 1
        continue
      }
      if (!meta[asset]) {
        meta[asset] = { conditionId: cid, eventSlug: r.eventSlug || '', title: r.title || '', outcome: r.outcome || '' }
      }
      flows.redeems++
      flows.redeemProceeds += usd
      consume((queues[asset] = queues[asset] || []), size, size ? usd / size : 1, ts, 'redeem', asset, meta, lots)
    } else if (r.type === 'MERGE') {
      // A merge hands back N shares of EVERY outcome and receives N USDC. It is
      // a real exit, and a wallet that uses it (RN1: 2,845 merges) otherwise
      // shows those positions rotting to zero — we measured −$14.2M of
      // "expired worthless" that was nothing of the sort.
      //
      // The dollar a merged bundle returns is split across the legs in
      // proportion to what each leg COST. Total P&L is 1 − Σentry however it is
      // split; only the per-band attribution depends on this convention, and
      // cost-proportional is the one that leaves a break-even bundle
      // break-even on every leg instead of inventing a winner and a loser.
      const cid = r.conditionId || ''
      const legs = jsonList(markets[cid]?.clobTokenIds)
        .map((_, i) => tokenOf[`${cid}|${i}`])
        .filter(Boolean)
      if (!legs.length || size <= 0) {
        unhandled['MERGE (no token map)'] = (unhandled['MERGE (no token map)'] || 0) + 1
        continue
      }
      const peeks = legs.map((a) => peekCost((queues[a] = queues[a] || []), size))
      const total = sum(peeks)
      flows.merges++
      flows.mergeProceeds += usd
      legs.forEach((asset, i) => {
        if (!meta[asset]) {
          meta[asset] = { conditionId: cid, eventSlug: r.eventSlug || '', title: r.title || '', outcome: '' }
        }
        const share = total ? peeks[i] / total : 1 / legs.length
        consume(queues[asset], size, (usd * share) / size, ts, 'merge', asset, meta, lots)
      })
    } else if (r.type === 'MAKER_REBATE' || r.type === 'TAKER_REBATE') {
      flows.rebates += usd
    } else if (r.type === 'REWARD') {
      flows.rewards += usd
    } else {
      unhandled[r.type] = (unhandled[r.type] || 0) + 1
    }
  }

  for (const [asset, q] of Object.entries(queues)) {
    if (!q.length) continue
    const mm = meta[asset] || {}
    const m = markets[mm.conditionId]
    const res = resolution(m)
    const outcome = mm.outcome || ''
    let value: number
    let kind: Lot['exitKind']
    if (res) {
      value = res[outcome] ?? 0
      kind = 'resolved'
    } else {
      const mk = mark(m, outcome)
      value = mk === null ? 0 : mk
      kind = 'open'
    }
    for (const [shares, price, ts] of q) {
      flows.openValue += shares * value
      lots.push({
        asset, conditionId: mm.conditionId || '', eventSlug: mm.eventSlug || '',
        title: mm.title || '', outcome, shares, entryPrice: price, entryTs: ts,
        exitPrice: value, exitTs: null, exitKind: kind, entryMinute: null,
      })
    }
  }

  for (const lot of lots) {
    const ko = kickoffTs(markets[lot.conditionId])
    if (ko) lot.entryMinute = (lot.entryTs - ko) / 60
  }
  return { lots, flows }
}

// ─── metrics ────────────────────────────────────────────────────────────────

const pct = (x: number, of: number) => (!of ? 0 : (100 * x) / of)
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0)

// ⚠️ NOT Math.max(...xs). Spreading an array of 190k fills — which is what a
// market maker's history is — overflows the call stack, and the failure looks
// like a server fault rather than a size limit.
const maxOf = (xs: number[], fallback = 0) => xs.reduce((a, b) => (b > a ? b : a), xs.length ? xs[0] : fallback)
const minOf = (xs: number[], fallback = 0) => xs.reduce((a, b) => (b < a ? b : a), xs.length ? xs[0] : fallback)

function median(xs: number[]): number {
  if (!xs.length) return 0
  const ys = [...xs].sort((a, b) => a - b)
  const mid = ys.length >> 1
  return ys.length % 2 ? ys[mid] : (ys[mid - 1] + ys[mid]) / 2
}

function quantile(xs: number[], q: number): number {
  if (!xs.length) return 0
  const ys = [...xs].sort((a, b) => a - b)
  return ys[Math.min(ys.length - 1, Math.max(0, Math.round(q * (ys.length - 1))))]
}

function timingWindow(minute: number | null): string {
  if (minute === null) return 'unknown'
  if (minute < WIN_IN_MATCH[0]) return 'prematch'
  if (minute < WIN_IN_MATCH[1]) return 'in_match'
  if (minute < WIN_WHISTLE[1]) return 'whistle'
  return 'settle'
}

function bucket(lots: Lot[], key: (l: Lot) => string) {
  const agg: Record<string, { lots: number; cost: number; pnl: number }> = {}
  for (const l of lots) {
    const k = key(l)
    const a = (agg[k] = agg[k] || { lots: 0, cost: 0, pnl: 0 })
    a.lots++
    a.cost += cost(l)
    a.pnl += lotPnl(l)
  }
  const total = sum(Object.values(agg).map((a) => a.pnl))
  return Object.entries(agg)
    .map(([label, a]) => ({
      label, lots: a.lots, cost: a.cost, pnl: a.pnl,
      return_pct: pct(a.pnl, a.cost), share_of_pnl: pct(a.pnl, total),
    }))
    .sort((x, y) => y.cost - x.cost)
}

/** Peak/median open cost basis and the cumulative cash floor — how much money
 *  the wallet ever actually had to put in, which the P&L cannot say. */
function exposureOf(lots: Lot[]) {
  const events: [number, number, number][] = []
  for (const l of lots) {
    events.push([l.entryTs, cost(l), -cost(l)])
    if (l.exitTs !== null) events.push([l.exitTs, -cost(l), proceeds(l)])
  }
  events.sort((a, b) => a[0] - b[0])
  let open = 0, cash = 0, peak = 0, floor = 0
  const series: number[] = []
  for (const [, dOpen, dCash] of events) {
    open += dOpen
    cash += dCash
    peak = Math.max(peak, open)
    floor = Math.min(floor, cash)
    series.push(open)
  }
  return { peak_cost_basis: peak, median_cost_basis: median(series), cash_floor: floor, final_cash: cash }
}

/** Yield CI resampled over EVENTS, not lots — lots inside one fixture are the
 *  same bet taken repeatedly, and pooling them turns ±20pp into a fake ±3pp. */
function bootstrap(lots: Lot[], seed = 0x9e3779b9, draws = 4000) {
  const byEvent: Record<string, [number, number]> = {}
  for (const l of lots) {
    const k = l.eventSlug || l.conditionId || '?'
    const e = (byEvent[k] = byEvent[k] || [0, 0])
    e[0] += cost(l)
    e[1] += lotPnl(l)
  }
  const keys = Object.keys(byEvent).sort()
  if (keys.length < 5) {
    return { events: keys.length, yield_pct: 0, ci_lo: null, ci_hi: null, p_le_zero: null }
  }
  const costs = keys.map((k) => byEvent[k][0])
  const pnls = keys.map((k) => byEvent[k][1])
  const n = keys.length
  const point = pct(sum(pnls), sum(costs))
  const rng = mulberry32(seed)
  const ys: number[] = []
  let neg = 0
  for (let d = 0; d < draws; d++) {
    let c = 0, p = 0
    for (let i = 0; i < n; i++) {
      const j = Math.min(n - 1, Math.floor(rng() * n))
      c += costs[j]
      p += pnls[j]
    }
    const y = pct(p, c)
    ys.push(y)
    if (y <= 0) neg++
  }
  ys.sort((a, b) => a - b)
  return {
    events: n, yield_pct: point,
    ci_lo: ys[Math.floor(0.025 * ys.length)], ci_hi: ys[Math.floor(0.975 * ys.length)],
    p_le_zero: neg / draws,
  }
}

export function analyse(
  wallet: string, rows: ActivityRow[], markets: Record<string, GammaMarket>,
  complete = true, lbProfit: number | null = null, currentValue: number | null = null,
  sports: Sports = {},
): WalletProfile {
  const { lots: allLots, flows } = buildLots(rows, markets)
  if (!allLots.length) throw new Error('no market activity could be reconstructed for this wallet')

  // Unmatched shares arrived off-feed. They carry zero P&L but would carry cost
  // the wallet never paid, which makes every share-of-capital line sum past
  // 100%. Held out of every aggregation, reported on their own.
  const unmatched = allLots.filter((l) => l.exitKind === 'unmatched')
  const lots = allLots.filter((l) => l.exitKind !== 'unmatched')

  const ident = [...rows].reverse().find((r) => r.name || r.pseudonym) || ({} as ActivityRow)
  const deployed = flows.deployed
  const pnl = sum(lots.map(lotPnl))
  const pnlHigh = pnl + sum(unmatched.map(proceeds))

  const closed = lots.filter((l) => l.exitTs !== null)
  const marks = lots.filter((l) => l.exitTs === null)
  const holds = closed.map(holdMin).filter((h): h is number => h !== null)

  const stamps = rows.map((r) => r.timestamp)
  const firstTs = minOf(stamps)
  const lastTs = maxOf(stamps)
  const dayOf = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)
  const daysActive = new Set(stamps.map(dayOf)).size

  const daily: Record<string, number> = {}
  for (const l of lots) {
    const d = dayOf(l.exitTs ?? l.entryTs)
    daily[d] = (daily[d] || 0) + lotPnl(l)
  }
  const dayVals = Object.keys(daily).sort().map((d) => daily[d])
  let streak = 0, worstStreak = 0
  for (const v of dayVals) {
    streak = v < 0 ? streak + 1 : 0
    worstStreak = Math.max(worstStreak, streak)
  }

  const thisMonth = new Date().toISOString().slice(0, 7)
  const monthAgg: Record<string, any> = {}
  for (const l of lots) {
    const k = new Date(l.entryTs * 1000).toISOString().slice(0, 7)
    const m = (monthAgg[k] = monthAgg[k] || {
      deployed: 0, pnl: 0, lots: 0, events: new Set<string>(), prematchCost: 0, entryW: 0, hold: [] as number[],
    })
    m.deployed += cost(l)
    m.pnl += lotPnl(l)
    m.lots++
    m.events.add(l.eventSlug || l.conditionId)
    if (timingWindow(l.entryMinute) === 'prematch') m.prematchCost += cost(l)
    m.entryW += l.entryPrice * cost(l)
    const h = holdMin(l)
    if (h !== null) m.hold.push(h)
  }
  const months = Object.keys(monthAgg).sort().map((k) => {
    const m = monthAgg[k]
    return {
      month: k, partial: k === thisMonth, deployed: m.deployed, pnl: m.pnl,
      yield_pct: pct(m.pnl, m.deployed), lots: m.lots, events: m.events.size,
      prematch_share: pct(m.prematchCost, m.deployed),
      mean_entry: m.deployed ? m.entryW / m.deployed : 0,
      median_hold_min: median(m.hold),
    }
  })

  const byEvent: Record<string, number> = {}
  for (const l of lots) {
    const k = l.eventSlug || l.conditionId
    byEvent[k] = (byEvent[k] || 0) + lotPnl(l)
  }
  const ranked = Object.values(byEvent).sort((a, b) => b - a)
  const topLots = lots.map(lotPnl).sort((a, b) => b - a)
  const drop200 = pnl - sum(topLots.slice(0, 200))

  const sweeps = closed.filter((l) => l.entryPrice <= 0.15 && l.exitPrice >= 3 * Math.max(l.entryPrice, 1e-9))
  const sweepExamples = [...sweeps].sort((a, b) => lotPnl(b) - lotPnl(a)).slice(0, 6)

  const inMatch = closed.filter((l) => timingWindow(l.entryMinute) === 'in_match')
  const moves = MOVE_BANDS.map(([label, lo, hi]) => {
    const sel = inMatch.filter((l) => l.exitPrice - l.entryPrice >= lo && l.exitPrice - l.entryPrice < hi)
    return { label, lots: sel.length, cost: sum(sel.map(cost)), pnl: sum(sel.map(lotPnl)) }
  })

  const timing: Record<string, any> = {}
  for (const win of ['prematch', 'in_match', 'whistle', 'settle', 'unknown']) {
    const sel = lots.filter((l) => timingWindow(l.entryMinute) === win)
    const c = sum(sel.map(cost))
    const p = sum(sel.map(lotPnl))
    timing[win] = { lots: sel.length, cost: c, pnl: p, return_pct: pct(p, c), share_of_cost: pct(c, deployed) }
  }

  const entryBands = ENTRY_BANDS.map(([lo, hi]) => {
    const sel = lots.filter((l) => l.entryPrice >= lo && l.entryPrice < hi)
    const c = sum(sel.map(cost))
    const p = sum(sel.map(lotPnl))
    return {
      label: `${lo.toFixed(2)}–${hi.toFixed(2)}`, lo, hi, lots: sel.length,
      cost: c, pnl: p, return_pct: pct(p, c), share_of_pnl: pct(p, pnl),
    }
  })

  // A ticket is what the wallet sent to the book. FIFO splits one buy across
  // several exits, so lot costs are fragments and their median runs far low.
  const buyTickets = rows
    .filter((r) => r.type === 'TRADE' && r.side === 'BUY' && Number(r.usdcSize || 0) > 0)
    .map((r) => Number(r.usdcSize))
  let footballCost = 0
  let unknownSportCost = 0
  for (const l of lots) {
    const verdict = football(markets[l.conditionId], sports)
    if (verdict === true) footballCost += cost(l)
    else if (verdict === null) unknownSportCost += cost(l)
  }
  const buyShares = sum(lots.map((l) => l.shares))
  const sellLots = closed.filter((l) => l.exitKind === 'sell')
  const exposure = exposureOf(lots)
  const sweepCost = sum(sweeps.map(cost))
  const sweepPnl = sum(sweeps.map(lotPnl))
  // PM's leaderboard is GROSS of fees and leaves rebates out — see the Python.
  const feesPaid = flows.fees
  const reconTol = lbProfit !== null ? Math.max(1, RECON_TOL_PCT * Math.abs(lbProfit)) : 1

  const profile: WalletProfile = {
    wallet: wallet.toLowerCase(),
    name: ident.name || '',
    pseudonym: ident.pseudonym || '',
    profile_image: ident.profileImage || '',
    generated_at: new Date().toISOString().slice(0, 19),
    coverage: {
      rows: rows.length, complete, first_ts: firstTs, last_ts: lastTs,
      days_span: Math.max(1, Math.round((lastTs - firstTs) / 86400)),
      days_active: daysActive,
      markets: new Set(lots.map((l) => l.conditionId)).size,
      events: new Set(lots.map((l) => l.eventSlug || l.conditionId)).size,
      tokens: new Set(lots.map((l) => l.asset)).size,
      metadata_missing: new Set(lots.filter((l) => !markets[l.conditionId]).map((l) => l.conditionId)).size,
      unhandled: flows.unhandled,
      unmatched_lots: unmatched.length,
      unmatched_proceeds: sum(unmatched.map(proceeds)),
    },
    totals: {
      fills: flows.buys + flows.sells, buys: flows.buys, sells: flows.sells, redeems: flows.redeems,
      deployed, sell_proceeds: flows.sellProceeds, redeem_proceeds: flows.redeemProceeds,
      open_value: flows.openValue, pnl, pnl_high: pnlHigh,
      yield_pct: pct(pnl, deployed), yield_high_pct: pct(pnlHigh, deployed),
      realized_pnl: sum(closed.map(lotPnl)), marked_pnl: sum(marks.map(lotPnl)),
      rebates: flows.rebates, rewards: flows.rewards, fees: feesPaid,
      median_ticket: median(buyTickets), p90_ticket: quantile(buyTickets, 0.9),
      max_ticket: maxOf(buyTickets),
      buy_vwap: buyShares ? deployed / buyShares : 0,
      sell_vwap: sellLots.length ? sum(sellLots.map(proceeds)) / sum(sellLots.map((l) => l.shares)) : 0,
      fills_per_active_day: (flows.buys + flows.sells) / Math.max(1, daysActive),
      current_value: currentValue,
    },
    reconciliation: {
      lb_profit: lbProfit,
      basis: 'gross of fees, rebates excluded',
      reconstructed: pnl + feesPaid,
      reconstructed_high: pnlHigh + feesPaid,
      tolerance: reconTol,
      inside_bracket:
        lbProfit !== null &&
        pnl + feesPaid - reconTol <= lbProfit &&
        lbProfit <= pnlHigh + feesPaid + reconTol,
    },
    exposure: { ...exposure, turnover: exposure.peak_cost_basis ? deployed / exposure.peak_cost_basis : 0 },
    hold: {
      median_min: median(holds), p90_min: quantile(holds, 0.9),
      under_10min_pct: pct(holds.filter((h) => h <= 10).length, holds.length),
      over_1day_pct: pct(holds.filter((h) => h > 1440).length, holds.length),
    },
    exits: {
      sell_pct: pct(lots.filter((l) => l.exitKind === 'sell').length, lots.length),
      redeem_pct: pct(lots.filter((l) => l.exitKind === 'redeem').length, lots.length),
      merge_pct: pct(lots.filter((l) => l.exitKind === 'merge').length, lots.length),
      resolved_pct: pct(lots.filter((l) => l.exitKind === 'resolved').length, lots.length),
      open_pct: pct(lots.filter((l) => l.exitKind === 'open').length, lots.length),
      expired_worthless_pnl: sum(lots.filter((l) => l.exitKind === 'resolved' && l.exitPrice === 0).map(lotPnl)),
    },
    timing,
    entry_bands: entryBands,
    moves,
    months,
    days: {
      n: dayVals.length,
      winning: dayVals.filter((v) => v > 0).length,
      losing: dayVals.filter((v) => v < 0).length,
      best: maxOf(dayVals),
      worst: minOf(dayVals),
      worst_losing_streak: worstStreak,
    },
    universe: bucket(lots, (l) => competition(markets[l.conditionId], sports)).slice(0, 16),
    market_types: bucket(lots, (l) => markets[l.conditionId]?.sportsMarketType || 'other').slice(0, 10),
    concentration: {
      top1_pct: pct(sum(ranked.slice(0, 1)), pnl),
      top5_pct: pct(sum(ranked.slice(0, 5)), pnl),
      top10_pct: pct(sum(ranked.slice(0, 10)), pnl),
      profitable_events_pct: pct(Object.values(byEvent).filter((v) => v > 0).length, Object.keys(byEvent).length),
      drop_top200_pnl: drop200, drop_top200_yield_pct: pct(drop200, deployed),
    },
    sweeps: {
      lots: sweeps.length, events: new Set(sweeps.map((l) => l.eventSlug)).size,
      cost: sweepCost, pnl: sweepPnl, return_pct: pct(sweepPnl, sweepCost),
      share_of_pnl: pct(sweepPnl, pnl), share_of_cost: pct(sweepCost, deployed),
      median_ticket: median(sweeps.map(cost)),
      median_hold_min: median(sweeps.map(holdMin).filter((h): h is number => h !== null)),
      examples: sweepExamples.map((l) => ({
        title: l.title, outcome: l.outcome, shares: l.shares, entry: l.entryPrice,
        exit: l.exitPrice, pnl: lotPnl(l), hold_min: holdMin(l), kind: l.exitKind,
      })),
    },
    bootstrap: bootstrap(lots),
    sport: { football_pct: pct(footballCost, deployed), unknown_pct: pct(unknownSportCost, deployed) },
    trust: 'ok',
    archetypes: [],
    narrative: { headline: '', sections: [] },
  }
  // How much of this report can be believed, in one field. Everything that
  // reads a number out loud checks it first.
  const rec = profile.reconciliation
  profile.trust = !complete
    ? 'partial'
    : rec.lb_profit !== null && !rec.inside_bracket
      ? 'unreconciled'
      : 'ok'
  profile.archetypes = classify(profile)
  profile.narrative = narrate(profile)
  return profile
}

// ─── reading the wallet ─────────────────────────────────────────────────────
//
// Every sentence below is a threshold on a number that appears in the profile,
// and no sentence is written without the number that licensed it. The same
// rules live in agent/wallet_analyzer.py — keep them in step.

export function fmtMoney(x: number): string {
  const sign = x < 0 ? '-' : ''
  const a = Math.abs(x)
  return a >= 1000
    ? `${sign}$${a.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    : `${sign}$${a.toFixed(2)}`
}

export function fmtDur(minutes: number): string {
  if (minutes < 60) return `${minutes.toFixed(1)} min`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} h`
  return `${(minutes / 1440).toFixed(1)} days`
}

const sgn = (x: number, dp = 1) => `${x >= 0 ? '+' : ''}${x.toFixed(dp)}%`

export function classify(p: WalletProfile) {
  const t = p.totals, tim = p.timing, hold = p.hold
  const dep = Math.max(t.deployed, 1e-9)
  const out: WalletProfile['archetypes'] = []
  const add = (key: string, label: string, confidence: string, evidence: string[]) =>
    out.push({ key, label, confidence, evidence })

  const prematch = tim.prematch.share_of_cost
  const live = tim.in_match.share_of_cost + tim.whistle.share_of_cost + tim.settle.share_of_cost

  const sw = p.sweeps
  if (sw.lots >= 20 && sw.share_of_pnl >= 25) {
    add('sweeper', 'Stale-order sweeper', sw.share_of_pnl >= 40 ? 'high' : 'medium', [
      `${sw.lots.toLocaleString('en-US')} lots entered at ≤0.15 and exited at ≥3× carry ` +
        (sw.share_of_pnl > 100
          ? `MORE than all of the profit (${sw.share_of_pnl.toFixed(0)}%) — the rest of the book loses money — on ${sw.share_of_cost.toFixed(1)}% of the capital`
          : `${sw.share_of_pnl.toFixed(0)}% of all profit on ${sw.share_of_cost.toFixed(1)}% of the capital`),
      `median sweep ticket ${fmtMoney(sw.median_ticket)}, median hold ${fmtDur(sw.median_hold_min)}, across ${sw.events} events`,
    ])
  }

  if (live >= 80 && hold.median_min <= 60) {
    add('inplay_scalper', 'In-play short-horizon trader', 'high', [
      `${live.toFixed(1)}% of capital goes in after kick-off, ${prematch.toFixed(1)}% before`,
      `median hold ${fmtDur(hold.median_min)}, ${hold.under_10min_pct.toFixed(0)}% of round trips close inside 10 minutes`,
    ])
  } else if (prematch >= 60) {
    add('prematch', 'Pre-match position taker', 'high', [
      `${prematch.toFixed(1)}% of capital is committed before kick-off`,
      `median hold ${fmtDur(hold.median_min)}`,
    ])
  }

  const football = p.sport?.football_pct ?? 100
  if (football >= 50 && tim.whistle.share_of_cost + tim.settle.share_of_cost >= 15) {
    const share = tim.whistle.pnl + tim.settle.pnl
    add('post_whistle', 'Post-whistle settlement buyer', pct(share, t.pnl) >= 30 ? 'high' : 'medium', [
      `${(tim.whistle.share_of_cost + tim.settle.share_of_cost).toFixed(1)}% of capital is deployed after minute 110 — when the result is already public`,
      `that capital returns ${sgn(tim.whistle.return_pct)} (whistle) / ${sgn(tim.settle.return_pct)} (post-settlement)`,
    ])
  }

  const cheap = sum(p.entry_bands.filter((b) => b.hi <= 0.15).map((b) => b.cost))
  const rich = sum(p.entry_bands.filter((b) => b.lo >= 0.9).map((b) => b.cost))
  if (pct(cheap, dep) >= 35) {
    add('longshot', 'Longshot buyer', 'high', [`${pct(cheap, dep).toFixed(0)}% of capital enters below 0.15`])
  }
  if (pct(rich, dep) >= 35) {
    add('favourite_grinder', 'Near-certainty grinder', 'high', [
      `${pct(rich, dep).toFixed(0)}% of capital enters above 0.90 — thin margins, high turnover`,
      `capital recycled ${p.exposure.turnover.toFixed(1)}× against a peak book of ${fmtMoney(p.exposure.peak_cost_basis)}`,
    ])
  }

  if (t.rebates > 0 && t.rebates >= 0.02 * Math.abs(t.pnl || 1)) {
    add('maker', 'Earns liquidity rebates', 'medium', [
      `${fmtMoney(t.rebates)} of maker/taker rebates — ${pct(t.rebates, Math.abs(t.pnl) || 1).toFixed(0)}% the size of trading P&L`,
    ])
  }

  if (p.concentration.top5_pct >= 60) {
    add('concentrated', 'Concentrated punter', 'high', [
      `the top 5 events carry ${p.concentration.top5_pct.toFixed(0)}% of all profit — this is a handful of bets, not a process`,
    ])
  }

  if (p.coverage.events >= 200 && t.fills_per_active_day >= 30) {
    add('systematic', 'Systematic / automated', 'high', [
      `${t.fills.toLocaleString('en-US')} fills across ${p.coverage.events.toLocaleString('en-US')} events, ${t.fills_per_active_day.toFixed(0)} fills per active day`,
      `median ticket ${fmtMoney(t.median_ticket)} — size is uniform, which is what a script looks like`,
    ])
  }

  if (!out.length) {
    add('unclassified', 'No clear archetype', 'low', ['none of the archetype thresholds fired — read the tables directly'])
  }
  return out
}

export function narrate(p: WalletProfile) {
  const t = p.totals, tim = p.timing, cov = p.coverage
  const { hold, exposure: exp, concentration: conc, bootstrap: boot } = p
  const name = p.name || p.wallet.slice(0, 10)
  const dep = Math.max(t.deployed, 1e-9)
  const sections: { title: string; paragraphs: string[] }[] = []

  const live = tim.in_match.share_of_cost + tim.whistle.share_of_cost + tim.settle.share_of_cost
  const warn =
    p.trust === 'partial'
      ? '⚠️ PARTIAL HISTORY — the activity walk did not reach the start of this account, so every ' +
        'figure below describes only the window we read, and the FIFO matching is missing the ' +
        'positions that were opened before it. '
      : p.trust === 'unreconciled'
        ? "⚠️ THIS RECONSTRUCTION DOES NOT RECONCILE with Polymarket's own profit figure for the " +
          'wallet, so the numbers below are wrong by an unknown amount — most likely it exits ' +
          'through an activity type the feed does not publish, such as a merge. Read the shape, ' +
          'not the totals. '
        : ''
  const headline =
    warn +
    `${name} deployed ${fmtMoney(t.deployed)} across ${cov.events.toLocaleString('en-US')} events in ` +
    `${cov.days_span} days and finished ${fmtMoney(t.pnl)} — a yield of ${sgn(t.yield_pct, 2)} on money at risk. ` +
    `${live.toFixed(0)}% of that capital went in after kick-off, the median position was held ` +
    `${fmtDur(hold.median_min)}, and the median ticket was ${fmtMoney(t.median_ticket)}.`

  // ── what it does ──
  let para: string[] = [`**Archetype: ${p.archetypes.map((a) => a.label).join(', ')}.**`]
  for (const a of p.archetypes) {
    para.push(`— *${a.label}* (${a.confidence} confidence): ${a.evidence.join('; ')}.`)
  }
  para.push(
    `It trades ${cov.markets.toLocaleString('en-US')} markets over ${cov.events.toLocaleString('en-US')} fixtures — ` +
      `${t.fills.toLocaleString('en-US')} fills, ${t.fills_per_active_day.toFixed(0)} per active day over ` +
      `${cov.days_active} days with activity. It buys at an average of ${t.buy_vwap.toFixed(3)} and sells at ${t.sell_vwap.toFixed(3)}.`,
  )
  const topUni = p.universe.slice(0, 4)
  if (topUni.length) {
    para.push(
      'Where it plays: ' +
        topUni.map((u) => `${u.label} (${pct(u.cost, dep).toFixed(0)}% of capital, ${sgn(u.return_pct, 0)})`).join(', ') + '.',
    )
  }
  const types = p.market_types.filter((m) => m.label !== 'other').slice(0, 4)
  if (types.length) {
    para.push('Market types: ' + types.map((m) => `${m.label} ${pct(m.cost, dep).toFixed(0)}%`).join(', ') + '.')
  }
  sections.push({ title: 'What it does', paragraphs: para })

  // ── where the money is ──
  para = []
  const windows: [string, any][] = [
    ['before kick-off', tim.prematch], ['in play', tim.in_match],
    ['in the 20 min after the whistle', tim.whistle], ['after settlement time', tim.settle],
  ]
  const eligible = windows.filter(([, w]) => w.cost > 0.02 * dep)
  if (eligible.length) {
    const [label, w] = eligible.reduce((a, b) => (b[1].pnl > a[1].pnl ? b : a))
    para.push(
      `**The money is made ${label}**: ${fmtMoney(w.cost)} deployed there returned ${fmtMoney(w.pnl)} ` +
        `(${sgn(w.return_pct)}), which is ${pct(w.pnl, t.pnl).toFixed(0)}% of all profit on ${w.share_of_cost.toFixed(0)}% of the capital.`,
    )
  }
  const bands = p.entry_bands.filter((b) => b.cost > 0.01 * dep).sort((a, b) => b.return_pct - a.return_pct)
  if (bands.length >= 2) {
    const hi = bands[0], lo = bands[bands.length - 1]
    para.push(
      `Return falls with the entry price: ${sgn(hi.return_pct, 0)} in the ${hi.label} band against ` +
        `${sgn(lo.return_pct, 0)} in ${lo.label}. ` +
        (hi.hi <= 0.4
          ? 'That is a wallet paid for taking prices nobody else wanted, not for being right more often.'
          : 'The profitable end is the expensive end — this is a wallet paid for conviction, not for cheapness.'),
    )
  }
  if (p.moves.length) {
    // The flat bucket is the one whose price did not move — NOT whichever
    // bucket happens to hold the most capital, which on some wallets is the
    // tail and produces a sentence comparing a bucket to itself.
    const flat = p.moves.find((m) => m.label.startsWith('flat'))
    const tail = p.moves[p.moves.length - 1]
    if (flat && flat !== tail && flat.cost > 0 && tail.lots) {
      para.push(
        `In-play, the modal trade goes nowhere: the ${flat.label} bucket is ${flat.lots.toLocaleString('en-US')} lots and ` +
          `${fmtMoney(flat.cost)} of capital for ${fmtMoney(flat.pnl)}. The ${tail.label} bucket is ` +
          `${tail.lots.toLocaleString('en-US')} lots and ${fmtMoney(tail.pnl)}. The tail pays for the bleed — any copy of ` +
          'this has to be sized for that, because most positions lose the spread.',
      )
    }
  }
  para.push(
    `Exits: ${p.exits.sell_pct.toFixed(0)}% sold back into the book, ` +
      // A wallet that never merges should not be told it merged 0% of the time.
      (p.exits.merge_pct >= 0.5 ? `${p.exits.merge_pct.toFixed(0)}% merged back into USDC, ` : '') +
      `${p.exits.redeem_pct.toFixed(0)}% redeemed at ` +
      `settlement, ${p.exits.resolved_pct.toFixed(0)}% simply left to resolve (${fmtMoney(p.exits.expired_worthless_pnl)} of that expired worthless).`,
  )
  sections.push({ title: 'Where the money comes from', paragraphs: para })

  // ── evolution ──
  para = []
  // The month still being lived is a fraction of a month; comparing it to a
  // full one reads as a collapse in turnover that never happened.
  const months = p.months.filter((m) => !m.partial)
  const partial = p.months.filter((m) => m.partial)
  if (months.length >= 2) {
    const first = months[0], last = months[months.length - 1]
    const scale = last.deployed > 1.3 * first.deployed ? 'grew' : last.deployed < 0.7 * first.deployed ? 'shrank' : 'held roughly steady'
    const half = Math.floor(months.length / 2)
    const early = pct(sum(months.slice(0, half).map((m) => m.pnl)), sum(months.slice(0, half).map((m) => m.deployed)) || 1)
    const late = pct(sum(months.slice(half).map((m) => m.pnl)), sum(months.slice(half).map((m) => m.deployed)) || 1)
    const trend = late > early + 2 ? 'improving' : late < early - 2 ? 'decaying' : 'flat'
    const verdict =
      trend === 'decaying'
        ? 'A strategy that fades over its own life is usually one that ran out of the thing it was picking up, or was copied.'
        : trend === 'improving'
          ? scale === 'shrank'
            ? 'Turnover fell while the yield rose, which reads as the wallet becoming more selective rather than more skilful.'
            : 'Yield rising while turnover holds or grows is the signature of a repeatable process rather than a lucky run.'
          : 'Yield roughly constant across the life of the wallet.'
    para.push(
      `Across ${months.length} complete months the book ${scale} from ${fmtMoney(first.deployed)} to ` +
        `${fmtMoney(last.deployed)} of monthly turnover, and the yield is **${trend}** — ${sgn(early)} over the ` +
        `first ${half} month(s) against ${sgn(late)} over the last ${months.length - half}. ${verdict}`,
    )
    if (partial.length) {
      para.push(
        `${partial[0].month} is still in progress (${fmtMoney(partial[0].deployed)} deployed, ` +
          `${sgn(partial[0].yield_pct)}) and is excluded from the trend.`,
      )
    }
    type Shift = { size: number; what: string; a: any; b: any }
    const shifts: Shift[] = []
    for (let i = 0; i + 1 < months.length; i++) {
      const a = months[i], b = months[i + 1]
      shifts.push({ size: Math.abs(b.prematch_share - a.prematch_share), what: 'timing', a, b })
      shifts.push({ size: Math.abs(b.mean_entry - a.mean_entry) * 100, what: 'price', a, b })
    }
    shifts.sort((x, y) => y.size - x.size)
    if (shifts.length && shifts[0].size >= 15) {
      const { what, a, b } = shifts[0]
      para.push(
        what === 'timing'
          ? `**Regime change in ${b.month}**: the share of capital committed before kick-off moved ` +
            `${a.prematch_share.toFixed(0)}% → ${b.prematch_share.toFixed(0)}%. That is a different strategy, ` +
            'not a different month — split any evaluation there.'
          : `**Regime change in ${b.month}**: the average entry price moved ${a.mean_entry.toFixed(2)} → ` +
            `${b.mean_entry.toFixed(2)}. It started buying a different kind of position; the earlier record ` +
            'does not describe the current one.',
      )
    }
    const bestM = months.reduce((a, b) => (b.yield_pct > a.yield_pct ? b : a))
    const worstM = months.reduce((a, b) => (b.yield_pct < a.yield_pct ? b : a))
    para.push(
      `Best month ${bestM.month} (${fmtMoney(bestM.pnl)}, ${sgn(bestM.yield_pct)}), worst ${worstM.month} ` +
        `(${fmtMoney(worstM.pnl)}, ${sgn(worstM.yield_pct)}).`,
    )
  }
  const d = p.days
  para.push(
    `Day to day it wins ${d.winning} of ${d.n} sessions — best ${fmtMoney(d.best)}, worst ${fmtMoney(d.worst)}, ` +
      `longest losing streak ${d.worst_losing_streak} days. Peak open book ${fmtMoney(exp.peak_cost_basis)}; the ` +
      `cumulative cash floor is ${fmtMoney(exp.cash_floor)}, ` +
      (exp.cash_floor > -0.25 * exp.peak_cost_basis
        ? 'so it never needed much more than its first stake — it funded itself out of its own winnings.'
        : 'so it had to carry a real bankroll, not just recycle winnings.'),
  )
  sections.push({ title: 'How it evolved', paragraphs: para })

  // ── is it real ──
  para = []
  if (boot.ci_lo !== null && boot.ci_lo !== undefined) {
    para.push(
      `Bootstrapped over ${boot.events.toLocaleString('en-US')} events (resampled by event, not by lot, because lots ` +
        `inside one fixture are the same bet taken repeatedly): yield ${sgn(boot.yield_pct, 2)}, 95% CI ` +
        `[${sgn(boot.ci_lo, 2)}, ${sgn(boot.ci_hi, 2)}], p(≤0) = ${boot.p_le_zero.toFixed(4)}. ` +
        (boot.ci_lo > 0
          ? "The interval clears zero — the edge in this wallet's own record is real."
          : '**The interval contains zero.** Whatever this wallet\'s headline profit, its record does not ' +
            'distinguish it from a wallet that got lucky at this size.'),
    )
  }
  para.push(
    `Concentration: top event ${conc.top1_pct.toFixed(0)}% of profit, top 5 ${conc.top5_pct.toFixed(0)}%, top 10 ` +
      `${conc.top10_pct.toFixed(0)}%; ${conc.profitable_events_pct.toFixed(0)}% of events profitable. ` +
      (conc.drop_top200_pnl >= 0
        ? `Drop the 200 best individual lots and it still makes ${fmtMoney(conc.drop_top200_pnl)} ` +
          `(${sgn(conc.drop_top200_yield_pct, 2)}). `
        : `Drop the 200 best individual lots and the rest of the book LOSES ${fmtMoney(Math.abs(conc.drop_top200_pnl))} ` +
          `(${sgn(conc.drop_top200_yield_pct, 2)}) — the entire result is those 200 trades. `) +
      (conc.top5_pct < 35
        ? 'The result does not depend on a handful of bets.'
        : '**A large share of the result is a handful of bets** — treat the headline as one draw.'),
  )
  const rec = p.reconciliation
  if (rec.lb_profit !== null && rec.lb_profit !== undefined) {
    if (cov.unmatched_lots) {
      para.push(
        `Reconciliation: Polymarket's own all-time profit for this wallet is ${fmtMoney(rec.lb_profit)}, counted ` +
          `before fees; on the same basis (P&L plus ${fmtMoney(t.fees)} of fees paid) our reconstruction ` +
          `brackets it at ${fmtMoney(rec.reconstructed)} … ${fmtMoney(rec.reconstructed_high)} ` +
          (rec.inside_bracket
            ? '(it falls inside — the reconstruction is trustworthy).'
            : '(**it falls outside — do not trust these numbers**; something in the reconstruction is wrong, ' +
              'most likely an activity type we do not model).'),
      )
    } else {
      const gap = rec.lb_profit - rec.reconstructed
      para.push(
        `Reconciliation: Polymarket says ${fmtMoney(rec.lb_profit)} all-time, counted before fees; on the ` +
          `same basis (P&L plus ${fmtMoney(t.fees)} of fees paid) we reconstruct ` +
          `${fmtMoney(rec.reconstructed)} — a gap of ${fmtMoney(gap)} ` +
          `(${pct(Math.abs(gap), Math.abs(rec.lb_profit) || 1).toFixed(1)}%). ` +
          (rec.inside_bracket
            ? 'Close enough to trust.'
            : '**That gap is large enough to matter — read the numbers as approximate.**'),
      )
    }
  }
  sections.push({ title: 'Is the edge real?', paragraphs: para })

  // ── caveats ──
  const flags: string[] = []
  if (!cov.complete) {
    flags.push('⚠️ The activity walk did not finish — this is a PARTIAL history and every total below is a lower bound.')
  }
  if (cov.unmatched_lots) {
    flags.push(
      `⚠️ ${cov.unmatched_lots} lots sold shares that never appear as a purchase (${fmtMoney(cov.unmatched_proceeds)} ` +
        "of proceeds) — neg-risk conversions, which Polymarket's activity feed does not publish. They are booked " +
        'FLAT, so they add no profit; the true figure is inside the bracket above, not at either end.',
    )
  }
  if (Object.keys(cov.unhandled || {}).length) {
    flags.push(
      `⚠️ Activity types not modelled: ${JSON.stringify(cov.unhandled)}. Any wallet that exits through them is mis-measured here.`,
    )
  }
  if (cov.metadata_missing) {
    flags.push(
      `⚠️ ${cov.metadata_missing} markets have no metadata — their lots carry no kick-off time and fall in the 'unknown' timing bucket.`,
    )
  }
  if (t.marked_pnl && Math.abs(t.marked_pnl) > 0.05 * Math.abs(t.pnl || 1)) {
    flags.push(
      `⚠️ ${fmtMoney(t.marked_pnl)} of the P&L is unsold positions marked at settlement or at last trade — a mark, not money.`,
    )
  }
  if ((p.sport?.football_pct ?? 100) < 50) {
    flags.push(
      `⚠️ Only ${p.sport.football_pct.toFixed(0)}% of this wallet's capital is in competitions we ` +
        `recognise as football (${p.sport.unknown_pct.toFixed(0)}% is unrecognised). The 110-130′ ` +
        'whistle window is a football fact and means nothing on the rest — read the timing table ' +
        'for football wallets only.',
    )
  }
  flags.push(
    '⚠️ Selection bias on the wallet itself. You are reading it because someone pointed at it. The statistics ' +
      'describe the edge in its own record; they cannot tell you how many identically-shaped wallets blew up unseen.',
  )
  flags.push(
    "⚠️ `lb-api` 'volume' is SHARES, not dollars, and its windowed figures do not reconcile. Only `window=all` is used here.",
  )
  if (t.median_ticket < 25 && p.archetypes.some((a) => a.key === 'sweeper')) {
    flags.push(
      `⚠️ Capacity: the median ticket is ${fmtMoney(t.median_ticket)} and the largest is ${fmtMoney(t.max_ticket)}. ` +
        'This strategy is bounded by what other people leave resting on the book — it does not scale by adding money.',
    )
  }
  sections.push({ title: 'What is not established', paragraphs: flags })

  return { headline, sections }
}

/** One call: fetch everything and analyse. */
export async function analyseWallet(wallet: string, since: number | null = null): Promise<WalletProfile> {
  const addr = wallet.trim().toLowerCase()
  const [{ rows, complete }, lbProfit, currentValue, sports] = await Promise.all([
    fetchActivity(addr, since),
    fetchLbProfit(addr),
    fetchCurrentValue(addr),
    fetchSports(),
  ])
  if (!rows.length) throw new Error('this address has no Polymarket activity')
  const markets = await fetchMarkets(rows.map((r) => r.conditionId || ''))
  return analyse(addr, rows, markets, complete, lbProfit, currentValue, sports)
}
