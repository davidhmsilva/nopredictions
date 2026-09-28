// A team page's URL: /team/<our teams.id>-<name>. The id is what the page
// reads; the name is there for the reader and the search engine, and a URL
// whose name part is stale or missing is redirected to the current one.
//
// Client-safe: the Game Center (a client component) builds these links.

export function nameSlug(name: string): string {
  return (
    name
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      // Letters NFKD does not decompose, same fold as lib/teamMatch.
      .replace(/ø/g, 'o').replace(/Ø/g, 'o').replace(/ß/g, 'ss').replace(/æ/g, 'ae').replace(/đ/g, 'd').replace(/ł/g, 'l')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'team'
  )
}

export function teamSlug(id: number | string, name: string): string {
  return `${Number(id)}-${nameSlug(name)}`
}

export function teamHref(id: number | string, name: string): string {
  return `/team/${teamSlug(id, name)}`
}

/** The id at the front of a slug, or null when there is none. */
export function idOfSlug(slug: string): number | null {
  const m = /^(\d{1,9})(?:-|$)/.exec(slug)
  return m ? Number(m[1]) : null
}
