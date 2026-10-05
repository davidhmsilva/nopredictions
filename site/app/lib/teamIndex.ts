// The search box's club list: what /api/teams sends and how a query is
// matched against it. Client-safe — the server builds the keys with the same
// `fold` the browser uses on the query, so the two can never disagree about
// what "Atlético" or "Mönchengladbach" become.

export interface TeamHit {
  /** Our teams.id */
  i: number
  /** Display name */
  n: string
  /** League */
  l: string
  /** League tier (1 = top flight), the tie-break between equal matches */
  t?: number | null
  /** Folded spellings: display, canonical, ESPN, Polymarket, the database's aliases */
  k: string[]
}

/** Lower case, accents off, the letters NFKD leaves alone folded by hand
 *  (the same fold as lib/teamMatch), punctuation to spaces. */
export function fold(s: string): string {
  return s
    .replace(/ø/g, 'o').replace(/Ø/g, 'o').replace(/ß/g, 'ss').replace(/æ/g, 'ae')
    .replace(/đ/g, 'd').replace(/ł/g, 'l').replace(/ı/g, 'i').replace(/İ/g, 'i')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

/** How well one folded spelling answers the query, 0 when it does not.
 *  Whole-name and word-prefix hits outrank a substring, so "inter" finds
 *  Inter before it finds "Internacional" and never finds "Winterthur". */
function keyScore(key: string, q: string, qWords: string[]): number {
  if (key === q) return 100
  if (key.startsWith(q)) return 80
  const words = key.split(' ')
  // Every word of the query begins a word of the name, in any order:
  // "man utd" → "man united", "real soc" → "real sociedad".
  if (qWords.every((w) => words.some((k) => k.startsWith(w)))) return 60 + Math.min(qWords.length, 5)
  return 0
}

/** The best `limit` clubs for a query, or none under two characters. */
export function searchTeams(list: TeamHit[], query: string, limit = 6): TeamHit[] {
  const q = fold(query)
  if (q.length < 2) return []
  const qWords = q.split(' ').filter(Boolean)
  const scored: Array<{ t: TeamHit; s: number }> = []
  for (const t of list) {
    let s = 0
    for (const k of t.k) s = Math.max(s, keyScore(k, q, qWords))
    if (s > 0) scored.push({ t, s })
  }
  // Equal matches: the higher division first ("nott" is Nottingham Forest
  // before Notts County), then the shorter name.
  return scored
    .sort(
      (a, b) =>
        b.s - a.s ||
        (a.t.t ?? 9) - (b.t.t ?? 9) ||
        a.t.n.length - b.t.n.length ||
        a.t.n.localeCompare(b.t.n)
    )
    .slice(0, limit)
    .map((x) => x.t)
}

let cached: Promise<TeamHit[]> | null = null

/** One request per page load, made the first time the box is focused. A
 *  failure is forgotten, so the next focus tries again. */
export function loadTeamIndex(): Promise<TeamHit[]> {
  if (!cached) {
    cached = fetch('/api/teams')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: { teams: TeamHit[] }) => j.teams ?? [])
      .catch((e) => {
        cached = null
        throw e
      })
  }
  return cached
}
