import type { PaperTrade } from '../lib/supabase'

/** The agent's own numbers, counted off the trades on the page below.
 *
 *  This was a scrolling marquee carrying four real figures and six slogans
 *  ("AI VS THE MARKET", "NO PREDICTIONS · JUST EDGES"). The site-wide tape now
 *  sits above the nav, and two marquees stacked is noise — so this keeps the
 *  counted figures, drops the slogans, and stops moving. "AGENT: SCANNING" went
 *  with them: whether a cron on another machine is running is not something
 *  this page can see, so it was a claim rather than a reading.
 */
export function AgentStats({ trades }: { trades?: PaperTrade[] }) {
  const all = trades ?? []
  const open = all.filter((t) => !t.resolved_at)
  const strategies = new Set(all.map((t) => t.strategy_id)).size

  // When the newest entry landed. An agent page that cannot say this is one you
  // cannot tell is still running.
  const newest = all
    .map((t) => new Date(t.placed_at).getTime())
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => b - a)[0]
  const last = newest
    ? new Date(newest).toLocaleDateString([], { day: 'numeric', month: 'short' })
    : null

  if (all.length === 0) return null

  return (
    <div className="ag-stats">
      <div className="np-wrap ag-stats-inner">
        {/* Settled count, record and P&L used to sit here too. They are the
            first thing the equity curve says, two hundred pixels below, and a
            number printed twice on one screen reads as two numbers. What is
            left is what the curve does not carry. */}
        <span className="ag-stat">
          <b className="np-num">{open.length}</b> OPEN NOW
        </span>
        <span className="ag-stat">
          <b className="np-num">{strategies}</b> {strategies === 1 ? 'ARM' : 'ARMS'} RUNNING
        </span>
        <span className={`ag-stat ${last ? '' : 'is-dim'}`}>
          <b className="np-num">{last ?? '—'}</b> LAST ENTRY
        </span>
        <span
          className="ag-stat-note"
          title="Gross of the Polymarket taker fee, which averages about 1.18pp of a position."
        >
          paper · 1u flat · gross of fees
        </span>
      </div>
    </div>
  )
}
