// The name a club is shown under, as against the name our database keys it by.
//
// `teams.canonical_name` is Football-Data's spelling, which is an abbreviation
// as often as a name: "Sp Lisbon", "Ath Madrid", "Nott'm Forest", "M'gladbach",
// "Sociedad". Nobody types those into a search box, and a page titled "Sp
// Lisbon Form, Stats & Odds" is found by nobody. `team_names.json` maps our id
// to ESPN's display name for every club listed on /teams, matched league by
// league, with a short list of hand overrides. It is written by
// `agent/team_display_names.py`; re-run it when promoted clubs arrive.
//
// Display only. Every join — the boards, the resolver, the models — keeps
// using the canonical name. A club missing from the file keeps its canonical
// name: a stale spelling is better than a wrong club.
//
// Client-safe: no database, one small JSON.

import NAMES from './team_names.json'

interface Entry {
  name: string
  short?: string
  abbr?: string
}

const TABLE = NAMES as Record<string, Entry>

/** The name to show for our `teams.id`, else the name we were given. */
export function displayName(id: number | string, fallback: string): string {
  return TABLE[String(Number(id))]?.name ?? fallback
}

/** Every spelling a reader might type for this club, beyond the ones in the
 *  database: ESPN's display name, its short name and its abbreviation. */
export function displayKeys(id: number | string): string[] {
  const e = TABLE[String(Number(id))]
  return e ? [e.name, e.short, e.abbr].filter((x): x is string => !!x) : []
}
