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
  const settled = all.filter((t) => !!t.resolved_at)
  const open = all.filter((t) => !t.resolved_at)
  const wins = settled.filter((t) => t.result === 'won').length
  const losses = settled.length - wins
  const pnl = settled.reduce(
    (s, t) => s + Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0),
    0
  )

  if (all.length === 0) return null

  return (
    <div className="ag-stats">
      <div className="np-wrap ag-stats-inner">
        <span className="ag-stat">
          <b className="np-num">{settled.length}</b> SETTLED
        </span>
        <span className="ag-stat">
          <b className="np-num">{open.length}</b> OPEN
        </span>
        <span className="ag-stat">
          <b className="np-num">
            {settled.length > 0 ? `${wins}W / ${losses}L` : '—'}
          </b>{' '}
          RECORD
        </span>
        <span className={`ag-stat ${pnl >= 0 ? 'is-good' : 'is-bad'}`}>
          <b className="np-num">
            {settled.length > 0 ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}u` : '—'}
          </b>{' '}
          P&amp;L
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
