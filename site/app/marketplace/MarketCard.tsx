'use client'

/** One published agent on a card: what it does in a line, who built it, how
 *  its record stands, what history said, and Copy. The whole card opens the
 *  agent's page; the Copy button sits beside the link, not inside it. */

import Link from 'next/link'
import type { AgentSummary } from '../lib/agents'
import { oddsText, useOddsFormat } from '../lib/display'
import { isInplaySpec } from '../lib/inplaySpec'
import { strategyLine } from '../lib/strategyText'
import { CopyButton, type Relation } from '../components/CopyButton'
import { Sparkline, StatusBadge, ago, pct, settledOf, tone, units } from '../agent/parts'

/** A record this short is mostly the draw (CLAUDE.md, rule 4). */
const VERDICT_N = 200

export function MarketCard({ a, relation }: { a: AgentSummary; relation: Relation }) {
  const fmt = useOddsFormat()
  const settled = settledOf(a)
  const bt = a.backtest?.stats
  const live = isInplaySpec(a.spec)
  return (
    <article className="mk-card">
      <Link href={`/marketplace/${a.id}`} className="mk-card-link">
        <div className="ag-card-top">
          <span className="ag-kind">{live ? 'Live rule' : 'Before kick-off'}</span>
          <StatusBadge a={a} />
        </div>
        <div className="ag-card-name">{a.name}</div>
        <div className="mk-card-by">
          by <b>{a.author}</b>
          {a.copies > 0 && <> · {a.copies.toLocaleString('en-US')} cop{a.copies === 1 ? 'y' : 'ies'}</>}
        </div>
        <div className="mk-card-line">{strategyLine(a.spec, (d) => oddsText(d, fmt))}</div>
        <Sparkline values={a.spark} />
        <div className="ag-tiles">
          <span className="ag-tile">
            <em>Bets</em>
            <b className="np-num">{settled}</b>
          </span>
          <span className="ag-tile">
            <em>W–L</em>
            <b className="np-num">{settled ? `${a.wins}–${a.losses}` : '—'}</b>
          </span>
          <span className="ag-tile">
            <em>P&amp;L</em>
            <b className={`np-num ${tone(settled ? a.pl_units : null)}`}>{settled ? units(a.pl_units) : '—'}</b>
          </span>
          <span className="ag-tile">
            <em>Yield</em>
            <b className={`np-num ${tone(a.yield_pct)}`}>{a.yield_pct != null ? pct(a.yield_pct) : '—'}</b>
          </span>
        </div>
        <div className="mk-card-foot">
          {bt && bt.n > 0 ? (
            <span>
              History: <b className={`np-num ${tone(bt.yieldPct)}`}>{pct(bt.yieldPct)}</b> over{' '}
              <span className="np-num">{bt.n.toLocaleString('en-US')}</span> past bets
            </span>
          ) : (
            <span>{live ? 'Live rule — no replay, the record is the test' : 'No backtest'}</span>
          )}
          <span>
            {settled > 0 && settled < VERDICT_N ? 'Early record · ' : ''}
            {a.last_bet_at ? `last bet ${ago(a.last_bet_at)}` : 'no bets yet'}
          </span>
        </div>
      </Link>
      <div className="mk-card-actions">
        <CopyButton id={a.id} relation={relation} compact />
      </div>
    </article>
  )
}
