// Calendar days in the READER's time zone. "Yesterday" for someone in Los
// Angeles is not yesterday in UTC, and a results page that splits a Sunday
// evening in California across two dates is the wrong page. Client-side only:
// the server runs in UTC and has no reader to ask (lib/display).

/** YYYY-MM-DD of an instant in the reader's zone. en-CA writes ISO order. */
export function localDate(d: Date): string {
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(d)
}

export function shiftDay(day: string, k: number): string {
  // Noon UTC, so no zone offset can push the arithmetic across a date line.
  const t = Date.parse(`${day}T12:00:00Z`) + k * 86_400_000
  return new Date(t).toISOString().slice(0, 10)
}

export function todayLocal(): string {
  return localDate(new Date())
}

export function yesterdayLocal(): string {
  return shiftDay(todayLocal(), -1)
}

/** "Sun 27 Sep" / "Sun, Sep 27" — a date alone, in the reader's locale. */
export function dayLabel(day: string, us: boolean): string {
  const d = new Date(`${day}T12:00:00Z`)
  return d.toLocaleDateString(us ? 'en-US' : 'en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
}
