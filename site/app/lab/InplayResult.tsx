'use client'

/** What the Lab shows for a live rule: the rule as the runner will read it,
 *  and why there is no history number above it. */

import Link from 'next/link'
import type { InplaySpec } from '../lib/inplaySpec'

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
  saved,
  onSave,
}: {
  result: InplayApiResult
  saved: { id?: number; error?: string; busy?: boolean } | null
  onSave: () => void
}) {
  const r = result.rule!
  return (
    <section className="bt-panel">
      <div className="bt-verdict bt-verdict-warn">LIVE RULE — NO REPLAY, THE RECORD IS THE TEST</div>
      <p className="bt-text">
        This rule reads the match as it is played, so it cannot be replayed over past seasons the way a
        pre-match theory can. Save it and switch it on: it paper-trades every live match that fits, and
        the record it builds is the result.
      </p>

      <div className="bt-label">IT BUYS WHEN</div>
      <ul className="lq-rule">
        {r.entry.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
      </ul>
      <div className="bt-label">AND GETS OUT</div>
      <p className="bt-text">{r.exit}</p>

      <div className="bt-save">
        {saved?.id ? (
          <p className="bt-text">
            Saved. <Link href={`/agent/${saved.id}`}>Open the agent</Link> and press Run it — it trades
            nothing until you do.
          </p>
        ) : (
          <>
            <button type="button" className="lp-btn-primary bt-submit" disabled={saved?.busy} onClick={onSave}>
              {saved?.busy ? 'SAVING…' : 'SAVE AS AN AGENT'}
            </button>
            <span className="bt-save-note">
              One paper bet per match, 1 unit at the Polymarket ask, from the moment you switch it on.
            </span>
            {saved?.error && <p className="bt-text bt-neg">{saved.error}</p>}
          </>
        )}
      </div>

      {(result.caveats?.length ?? 0) > 0 && (
        <div className="bt-caveats">
          <div className="bt-label">HONESTY NOTES</div>
          <ul>
            {result.caveats!.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
