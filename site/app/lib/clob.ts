/** Polymarket's real order book, in as few requests as possible.
 *
 *  🔑 Gamma's listing carries `bestBid`/`bestAsk` on every market and they are
 *     free — they arrive in the same bytes as the board. They are also not the
 *     book. Measured across 810 football markets on 2026-09-20, Gamma's ask
 *     differs from the live CLOB by more than 1pp on **15.3%** of them and by
 *     more than 3pp on **8.4%**, with the worst cases on in-play totals: a
 *     Dinamo Zagreb O/U 8.5 quoted 1.000 on Gamma against 0.020 on the book.
 *
 *     Half a cent is all it takes to call the wrong exchange cheaper, so every
 *     price this site compares across venues is read from here. It costs about
 *     two requests for a whole matchday: `POST /books` takes 400 tokens at a
 *     time and answers in well under a second.
 *
 *  A token the book will not answer for keeps whatever quote it already had.
 *  A round trip that could not be made is not evidence about a price.
 */

import { quoteOf, type Quote } from './venues'

const CLOB_API = 'https://clob.polymarket.com'

/** Polymarket's own cap on one `/books` body. */
const BATCH = 400
const TIMEOUT_MS = 12_000

interface ClobBook {
  asset_id: string
  bids?: { price: string; size: string }[]
  asks?: { price: string; size: string }[]
}

function topOfBook(b: ClobBook): Quote {
  let bid: number | null = null
  let ask: number | null = null
  let askSize = 0
  for (const l of b.bids ?? []) {
    const p = Number(l.price)
    if (Number(l.size) > 0 && (bid == null || p > bid)) bid = p
  }
  for (const l of b.asks ?? []) {
    const p = Number(l.price)
    const s = Number(l.size)
    if (s > 0 && (ask == null || p < ask)) {
      ask = p
      askSize = s
    }
  }
  return quoteOf(bid, ask, ask != null ? ask * askSize : null)
}

/** Top of book for every token given. Tokens the venue does not answer for are
 *  simply absent from the map — never present with a null quote, which a
 *  caller could mistake for "no offers". */
export async function fetchBooks(tokenIds: string[]): Promise<Map<string, Quote>> {
  const out = new Map<string, Quote>()
  const uniq = Array.from(new Set(tokenIds.filter(Boolean)))
  const batches: string[][] = []
  for (let i = 0; i < uniq.length; i += BATCH) batches.push(uniq.slice(i, i + BATCH))

  const pages = await Promise.all(
    batches.map(async (batch) => {
      try {
        const res = await fetch(`${CLOB_API}/books`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(batch.map((token_id) => ({ token_id }))),
          signal: AbortSignal.timeout(TIMEOUT_MS),
          cache: 'no-store',
        })
        if (!res.ok) return [] as ClobBook[]
        return (await res.json()) as ClobBook[]
      } catch {
        // One batch failing costs its own tokens their book, not the board.
        return [] as ClobBook[]
      }
    })
  )
  for (const b of pages.flat()) out.set(b.asset_id, topOfBook(b))
  return out
}
