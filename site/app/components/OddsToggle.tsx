'use client'

/** One control, and every price on the site follows it.
 *
 *  The buttons are written AS the format — +150, 2.50, 40% — rather than
 *  named, because a reader recognises their own odds faster than they read
 *  the word for them. The name is there for a screen reader and on hover.
 */

import { ODDS_FORMATS, setOddsFormat, useOddsFormat } from '../lib/display'

export function OddsToggle({ className = '' }: { className?: string }) {
  const current = useOddsFormat()
  return (
    <div className={`np-odds ${className}`} role="group" aria-label="Odds format">
      {ODDS_FORMATS.map((o) => (
        <button
          key={o.id}
          type="button"
          className={current === o.id ? 'is-on' : ''}
          aria-pressed={current === o.id}
          aria-label={o.name}
          title={o.title}
          onClick={() => setOddsFormat(o.id)}
        >
          {o.example}
        </button>
      ))}
    </div>
  )
}
