/** The Eastern date a game is filed under.
 *
 *  ESPN, Kalshi and every US schedule file a game by its ET calendar date, and
 *  it is the strongest cheap discriminator there is for joining two feeds: two
 *  different matches between the same two clubs on the same ET date do not
 *  happen. A 22:00Z kick-off and a 02:00Z one both land where the schedule
 *  puts them, which a UTC date would not.
 *
 *  Client-safe: the cross-venue merge runs in the browser.
 */

const ET = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/** `20260920`, or null when there is no instant to file. */
export function etDateOf(when: string | number | Date | null | undefined): string | null {
  if (when == null) return null
  const d = when instanceof Date ? when : new Date(when)
  if (Number.isNaN(d.getTime())) return null
  const p: Record<string, string> = {}
  for (const x of ET.formatToParts(d)) p[x.type] = x.value
  return `${p.year}${p.month}${p.day}`
}
