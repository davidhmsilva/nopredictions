/** How a probability is written as a price — the pure half of lib/display.
 *
 *  No React here on purpose: server components (metadata, structured data)
 *  import this, and a module that imports a hook cannot be imported there. */

export type OddsFormat = 'american' | 'decimal' | 'implied'

const MINUS = '−'

/** A probability as a price, in the reader's format.
 *
 *  Callers keep their own guards — what counts as settled, what counts as no
 *  price at all — because those differ from page to page. This decides only
 *  how a real price is written. */
export function priceText(p: number, f: OddsFormat): string {
  if (!(p > 0 && p < 1)) return '—'
  if (f === 'implied') {
    const pc = p * 100
    return pc < 1 || pc > 99 ? `${pc.toFixed(1)}%` : `${Math.round(pc)}%`
  }
  const dec = 1 / p
  return f === 'decimal' ? dec.toFixed(2) : americanText(dec)
}

/** Decimal odds in American form. Evens is +100, the way a US book prints it. */
export function americanText(dec: number): string {
  if (!(dec > 1)) return '—'
  return dec >= 2
    ? `+${Math.round((dec - 1) * 100)}`
    : `${MINUS}${Math.round(100 / (dec - 1))}`
}
