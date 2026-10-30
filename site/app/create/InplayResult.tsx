'use client'

/** What Create shows for a live rule: the strategy as the runner will read
 *  it, why there is no history number above it, and Deploy. */

import type { InplaySpec } from '../lib/inplaySpec'
import { StrategyCard } from '../components/StrategyCard'
import { KeepActions, type Kept } from './LabResult'

export interface InplayApiResult {
  ok: boolean
  supported?: boolean
  reason?: string
  suggestion?: string | null
  interpretation?: string
  rule?: { entry: string[]; exit: string; summary: string }
  spec?: InplaySpec
  caveats?: string[]
}

export function InplayResult({
  result,
  kept,
  onKeep,
}: {
  result: InplayApiResult
  kept: Kept | null
  onKeep: (deploy: boolean) => void
}) {
  return (
    <section className="lr" aria-live="polite">
      <div className="lr-title">A live rule — it reads the match as it is played</div>
      <h2 className="lr-head">Your agent is ready to deploy</h2>
      <div className="lr-verdict is-warn">
        <strong>No replay — its own record is the test</strong>
        <span>
          A rule that reads the match while it is played cannot be replayed over past seasons the way a
          pre-match idea can. Deploy it and it paper-trades every live match that fits; the record it
          builds is the result.
        </span>
      </div>

      {result.spec && <StrategyCard spec={result.spec} label="Your agent's strategy" />}

      <KeepActions kept={kept} onKeep={onKeep} />

      {(result.caveats?.length ?? 0) > 0 && (
        <div className="lr-detail">
          <div className="lr-block">
            <h3>Worth knowing</h3>
            <ul className="lr-notes">
              {result.caveats!.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}
