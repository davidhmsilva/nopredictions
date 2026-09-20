/** Are these two spellings the same club?
 *
 *  Lifted out of `gamecenter` unchanged, because the cross-venue merge now
 *  runs in the BROWSER as well as on the server — the Kalshi sweep is too slow
 *  to sit inside the board's own request — and importing `gamecenter` for one
 *  function would drag its whole api-football/CLOB/Gamma surface into the
 *  bundle. `gamecenter` re-exports these, so every existing caller is
 *  untouched and there is still exactly one implementation.
 *
 *  ⚠️ Substring containment looks adequate and is not: on 2026-08-14 it paired
 *     "River Plate" with "Platense" ("plate" is inside "platense") and
 *     "Minnesota United" with "Minnesota United II", and each wrong pair
 *     produced a confident double-digit price discrepancy. Tokens, scored
 *     against the longer name, with reserve and youth sides disqualified.
 */

import KALSHI_ALIASES from './kalshi_aliases.json'

/** Letters NFKD does not decompose. Ported from `fixture_match._LETTER_FOLD`
 *  and it has to stay identical: without it "HB Køge" normalises to "hb k ge"
 *  here and "hb koge" there, and the alias table only matches one of them. */
const LETTER_FOLD: Record<string, string> = {
  'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'œ': 'oe', 'Œ': 'oe',
  'å': 'a', 'Å': 'a', 'ß': 'ss', 'đ': 'd', 'Đ': 'd', 'ð': 'd', 'Ð': 'd',
  'ł': 'l', 'Ł': 'l', 'ı': 'i', 'İ': 'i', 'þ': 'th', 'Þ': 'th',
}

const NAME_NOISE = new Set([
  'fc', 'cf', 'ca', 'aa', 'sc', 'ac', 'as', 'sv', 'sk', 'fk', 'afc', 'bk', 'if',
  'cd', 'ud', 'sd', 'rc', 'cs', 'club', 'de', 'do', 'da', 'the',
  'ff', 'bc', 'gf', 'ik', 'aik', 'os', 'vf', 'kv', 'us', 'usl',
  'football', 'futbol', 'calcio', 'cp', 'cr', 'ec', 'sp',
])

/** Canonical, because the same reserve side is "Real Sociedad B" on Polymarket
 *  and "Real Sociedad II" on api-football. */
const SQUAD_CANON: Record<string, string> = {
  ii: 'reserve', b: 'reserve', reserves: 'reserve', iii: 'third',
  u17: 'u17', u18: 'u18', u19: 'u19', u20: 'u20', u21: 'u21', u23: 'u23',
  legends: 'legends', youth: 'youth', academy: 'academy',
  women: 'women', w: 'women',
}

export const MIN_SIDE_SCORE = 0.6

/** An abbreviation is short: "Man" for "Manchester" yes, "plate" for
 *  "platense" no. */
const MAX_ABBREV_LEN = 4

function fold(s: string): string {
  return s.replace(/[øØæÆœŒåÅßđĐðÐłŁıİþÞ]/g, (c) => LETTER_FOLD[c] ?? c)
}

function normTeam(s: string): string {
  return fold(s)
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .trim()
}

function teamTokens(name: string): { ident: string[]; markers: string[] } {
  const raw = normTeam(name).split(/\s+/).filter(Boolean)
  return {
    ident: raw.filter((t) => !(t in SQUAD_CANON) && !NAME_NOISE.has(t)),
    markers: Array.from(new Set(raw.filter((t) => t in SQUAD_CANON).map((t) => SQUAD_CANON[t]))).sort(),
  }
}

function tokenHit(a: string, b: string): boolean {
  if (a === b) return true
  const [short, long] = a.length <= b.length ? [a, b] : [b, a]
  return short.length >= 3 && short.length <= MAX_ABBREV_LEN && long.startsWith(short)
}

/** 0-1 similarity between two spellings of a club.
 *
 *  Full containment scores 1.0 — the feeds disagree by ADDING words rather than
 *  changing them ("Coventry City FC" vs "Coventry"), so every token of the
 *  shorter name appearing in the longer is what agreement looks like here.
 *  Otherwise it is the shared fraction of the LONGER name, which is what keeps
 *  "Real Salt Lake" away from "Real Monarchs" at 0.33. */
export function teamScore(a: string, b: string): number {
  const ta = teamTokens(a)
  const tb = teamTokens(b)
  if (ta.markers.join(',') !== tb.markers.join(',')) return 0
  if (!ta.ident.length || !tb.ident.length) return 0

  const [short, long] = ta.ident.length <= tb.ident.length
    ? [ta.ident, tb.ident] : [tb.ident, ta.ident]
  if (short.every((x) => long.some((y) => tokenHit(x, y)))) return 1

  const shared = ta.ident.filter((x) => tb.ident.some((y) => tokenHit(x, y))).length
  return shared / Math.max(ta.ident.length, tb.ident.length)
}

export function fixtureMatches(
  pmHome: string, pmAway: string, otherHome: string, otherAway: string
): boolean {
  return (
    teamScore(pmHome, otherHome) >= MIN_SIDE_SCORE &&
    teamScore(pmAway, otherAway) >= MIN_SIDE_SCORE
  )
}


// ── clubs one feed spells differently from another ───────────────────────────

/** The alias table's lookup key. Club suffixes are dropped, squad markers are
 *  NOT — "Benfica" and "Benfica B" have to stay different keys. Mirrors
 *  `fixture_match._norm_key`, and the two must agree character for character
 *  or the shared JSON matches on one side only. */
export function aliasKey(name: string): string {
  return normTeam(name)
    .split(/\s+/)
    .filter((t) => t && !NAME_NOISE.has(t))
    .join(' ')
}

const ALIASES: Map<string, string> = new Map(
  Object.entries((KALSHI_ALIASES as { aliases?: Record<string, string> }).aliases ?? {}).map(
    ([k, v]) => [aliasKey(k), aliasKey(v)]
  )
)

/** Are these two spellings the same club?
 *
 *  The alias table first, and it decides by EXACT EQUALITY of the canonical
 *  both names resolve to — never by scoring. That directness is what makes a
 *  hand-written table safe: "Leuven" can be aliased without the word
 *  swallowing every club that contains it.
 *
 *  ⚠️ The scorer underneath is not infallible in the other direction either.
 *     "Fortaleza FC" and "Chaco For Ever" score 1.00, because the abbreviation
 *     rule lets "For" prefix "Fortaleza". What contains that is the caller's
 *     requirement that BOTH sides of a fixture agree and that the pairing be
 *     unique — never this function on its own.
 */
export function sameClub(a: string, b: string): boolean {
  const ca = ALIASES.get(aliasKey(a))
  if (ca !== undefined && ca === ALIASES.get(aliasKey(b))) return true
  return teamScore(a, b) >= MIN_SIDE_SCORE
}

export function aliasCount(): number {
  return ALIASES.size
}
