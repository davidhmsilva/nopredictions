// A club's name where there is room for about two words — a tile, a legend.
//
// The old rule was "the first two words", which turned "1. FC Union Berlin"
// into "1. FC" on every tile of that fixture. Legal prefixes and numbers carry
// no identity, so they go first. One function for the server (which labels the
// headline markets) and the client (which draws legends): the "priced like
// this" block finds a headline BY its label, so two spellings of the same rule
// would silently drop a row.

const NOISE = new Set([
  'fc', 'afc', 'cf', 'sc', 'ac', 'as', 'ss', 'sv', 'vfb', 'vfl', 'tsg', 'rb',
  'cd', 'ud', 'sd', 'rc', 'sk', 'fk', 'bk', 'ca', 'club',
])
// "Olympique de Marseille" needs three words to be a name at all.
const PARTICLES = new Set(['de', 'del', 'da', 'do', 'di', 'of', 'la', 'le'])

export function shortTeam(name: string): string {
  const all = name.trim().split(/\s+/)
  const kept = all.filter((t) => !NOISE.has(t.toLowerCase()) && !/^\d+\.?$/.test(t))
  if (!kept.length) return all.slice(0, 2).join(' ')
  const n = kept.length > 2 && PARTICLES.has(kept[1].toLowerCase()) ? 3 : 2
  return kept.slice(0, n).join(' ')
}
