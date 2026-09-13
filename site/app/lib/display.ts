/** How a price and a time are written for the person reading them.
 *
 *  🔑 Every price this site holds is a PROBABILITY — a Polymarket share price
 *     is one — and closing odds from our own database are turned into one on
 *     the way in. This file is the only place a price becomes text, so the
 *     format follows the reader instead of whoever wrote the component.
 *
 *  The default follows the reader's CLOCK, never the browser's language: a US
 *  time zone gets American odds and a 12-hour clock, everywhere else decimal
 *  and a 24-hour one. Language is the wrong key — a Portuguese machine asks
 *  for "quarta, 9/09", which is why dropping-odds once pinned en-GB — and
 *  keying on the zone keeps the site in English for both readers.
 *
 *  ⚠️ Client only. The server has no reader and runs in UTC, so a time or a
 *     format decided during a server render is the wrong one, and then
 *     mismatches on hydration. The hook returns decimal on the server and the
 *     reader's own choice on the first client render; the time helpers are for
 *     components that render after their data has been fetched.
 */

import { useSyncExternalStore } from 'react'

export type OddsFormat = 'american' | 'decimal' | 'implied'

export const ODDS_FORMATS: { id: OddsFormat; name: string; example: string; title: string }[] = [
  {
    id: 'american',
    name: 'American odds',
    example: '+150',
    title: 'American odds: +150 wins $150 on a $100 stake, −200 stakes $200 to win $100',
  },
  {
    id: 'decimal',
    name: 'decimal odds',
    example: '2.50',
    title: 'Decimal odds: what comes back for every $1 staked, stake included',
  },
  {
    id: 'implied',
    name: 'implied probability',
    example: '40%',
    title: 'Implied probability: the price read as a chance',
  },
]

export function formatName(f: OddsFormat): string {
  return ODDS_FORMATS.find((o) => o.id === f)?.name ?? 'decimal odds'
}

// ── the reader's zone ────────────────────────────────────────────────────────

/** Time zones where American odds are what a bettor expects. Canonical IANA
 *  names, plus the legacy aliases a browser can still report. */
const US_ZONE =
  /^(?:America\/(?:New_York|Detroit|Chicago|Denver|Boise|Phoenix|Los_Angeles|Anchorage|Juneau|Sitka|Metlakatla|Yakutat|Nome|Adak|Menominee|Puerto_Rico|Indianapolis|Louisville|Indiana\/\w+|Kentucky\/\w+|North_Dakota\/\w+)|Pacific\/Honolulu|US\/\w+)$/

export function viewerZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

export function isUsViewer(): boolean {
  const z = viewerZone()
  return z != null && US_ZONE.test(z)
}

// ── the odds format, remembered per browser ──────────────────────────────────

const KEY = 'np.odds'
let current: OddsFormat | null = null
const listeners = new Set<() => void>()

function isFormat(v: unknown): v is OddsFormat {
  return v === 'american' || v === 'decimal' || v === 'implied'
}

function snapshot(): OddsFormat {
  if (current) return current
  let saved: string | null = null
  try {
    saved = window.localStorage.getItem(KEY)
  } catch {
    /* a private window or blocked storage: fall through to the default */
  }
  current = isFormat(saved) ? saved : isUsViewer() ? 'american' : 'decimal'
  return current
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange)
  // Changed in another tab: drop the cached value and read it again.
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return
    current = null
    onChange()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(onChange)
    window.removeEventListener('storage', onStorage)
  }
}

export function setOddsFormat(f: OddsFormat): void {
  current = f
  try {
    window.localStorage.setItem(KEY, f)
  } catch {
    /* it still applies to this page view; it just will not be remembered */
  }
  listeners.forEach((l) => l())
}

/** The reader's odds format. Decimal on the server, their own on the client. */
export function useOddsFormat(): OddsFormat {
  return useSyncExternalStore(subscribe, snapshot, (): OddsFormat => 'decimal')
}

// ── prices ───────────────────────────────────────────────────────────────────

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

/** Decimal odds — a closing price from our own database — in the reader's format. */
export function oddsText(dec: number | null | undefined, f: OddsFormat): string {
  return dec != null && dec > 1 ? priceText(1 / dec, f) : '—'
}

// ── times ────────────────────────────────────────────────────────────────────

/** en-US or en-GB: the word order and the clock. English either way. */
function locale(): string {
  return isUsViewer() ? 'en-US' : 'en-GB'
}

/** "1:00 PM" in New York; "09:05" rather than "9:05" on a 24-hour clock. */
function hourStyle(): '2-digit' | 'numeric' {
  return isUsViewer() ? 'numeric' : '2-digit'
}

/** "ET", "CT", "MT", "PT" — what a US schedule prints — and the engine's own
 *  short name everywhere else ("BST", "CEST", "GMT+1"). */
export function zoneLabel(at: Date = new Date()): string {
  try {
    const name =
      new Intl.DateTimeFormat(locale(), { timeZoneName: 'short' })
        .formatToParts(at)
        .find((x) => x.type === 'timeZoneName')?.value ?? ''
    const us = /^(E|C|M|P|AK|H)[SD]T$/.exec(name)
    return us ? `${us[1]}T` : name
  } catch {
    return ''
  }
}

/** 1:00 PM · 18:00 */
export function timeText(d: Date): string {
  return d.toLocaleTimeString(locale(), { hour: hourStyle(), minute: '2-digit' })
}

/** Sun 1:00 PM · Sun 18:00 */
export function dayTimeText(d: Date): string {
  return d.toLocaleString(locale(), { weekday: 'short', hour: hourStyle(), minute: '2-digit' })
}

/** Sep 13 · 13 Sep. With the year: Sep 13, 26 · 13 Sep 26. */
export function dateText(d: Date, withYear = false): string {
  return d.toLocaleDateString(
    locale(),
    withYear ? { day: 'numeric', month: 'short', year: '2-digit' } : { day: 'numeric', month: 'short' }
  )
}

/** A day heading: Sunday, Sep 13 · Sunday 13 Sept. */
export function dayHeading(d: Date): string {
  return d.toLocaleDateString(locale(), { weekday: 'long', month: 'short', day: 'numeric' })
}

/** A kick-off standing on its own, with the zone named: Sun, Sep 13, 1:00 PM ET. */
export function kickoffText(d: Date): string {
  const s = d.toLocaleString(locale(), {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: hourStyle(),
    minute: '2-digit',
  })
  const z = zoneLabel(d)
  return z ? `${s} ${z}` : s
}
