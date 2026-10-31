/** Every idea written on Create, and whether it became a strategy (db/084): the
 *  only record of what people ask for that we could not give them. Server only.
 *
 *  Never in the way of the answer: a database with a bad hour costs a row, not a
 *  second of the user's wait, and a failed insert costs nothing else. */

import { getSql } from './db'
import { within } from './deadline'

const NOTE_MS = 800

export async function noteIdea(row: {
  idea: string
  kind: 'prematch' | 'inplay'
  supported: boolean
  reason?: string | null
  suggestion?: string | null
  spec?: unknown
}): Promise<void> {
  const sql = getSql()
  await within(
    sql`
      insert into public.lab_ideas (idea, kind, supported, reason, suggestion, spec)
      values (${row.idea.slice(0, 1200)}, ${row.kind}, ${row.supported}, ${row.reason ?? null},
              ${row.suggestion ?? null}, ${row.spec ? sql.json(row.spec as never) : null})
    `.then(() => null),
    NOTE_MS,
  )
}
