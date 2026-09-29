import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { entitlement, refusalMessage } from '../../../lib/plan'
import { ClaudeDeclined, askJson } from '../../../lib/claudeJson'
import { MAX_QUESTIONS, MAX_THEORY, PLAN_SHAPE, PLAN_SYSTEM, PlanSchema } from '../../../lib/labQuestions'

export const maxDuration = 60

const hits = new Map<string, number[]>()
function rateLimited(key: string): boolean {
  const now = Date.now()
  const arr = (hits.get(key) ?? []).filter((t) => now - t < 60_000)
  arr.push(now)
  hits.set(key, arr)
  return arr.length > 10
}

export async function POST(request: Request) {
  let theory: string
  try {
    theory = String((await request.json())?.hypothesis ?? '').trim()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  if (!theory || theory.length > MAX_THEORY) {
    return NextResponse.json({ ok: false, error: `A theory is 1-${MAX_THEORY} characters.` }, { status: 400 })
  }

  // Same gate as the test itself, but only LOOKED at: asking questions costs
  // a model call and must not be free to the signed-out, yet it is not the
  // test the daily count is for. The use is claimed by the step that tests.
  const user = await currentUser()
  const ent = await entitlement(user?.id ?? null, 'lab')
  if (!ent.allowed) {
    return NextResponse.json(
      { ok: false, error: refusalMessage(ent), entitlement: ent },
      { status: ent.reason === 'signed_out' ? 401 : 402 },
    )
  }
  if (rateLimited(user!.id)) {
    return NextResponse.json({ ok: false, error: 'Too many theories in a minute — slow down.' }, { status: 429 })
  }

  try {
    const plan = await askJson(PlanSchema, PLAN_SYSTEM, `Theory: "${theory}"`, PLAN_SHAPE)
    const questions = plan.mode === 'unsupported' ? [] : plan.questions
      .filter((q) => q.options.length >= 2)
      .slice(0, MAX_QUESTIONS)
      .map((q) => {
        // Exactly one recommendation, and it goes first — the page relies on it.
        const i = Math.max(0, q.options.findIndex((o) => o.recommended))
        const opts = [q.options[i], ...q.options.filter((_, j) => j !== i)].slice(0, 4)
        return {
          header: q.header.slice(0, 14),
          question: q.question,
          options: opts.map((o, j) => ({ ...o, recommended: j === 0, clarification: o.clarification.slice(0, 120) })),
        }
      })
    return NextResponse.json({ ok: true, mode: plan.mode, questions })
  } catch (err) {
    // The questions are a help, not a gate: the page goes straight to the
    // test when this step fails.
    console.error('lab plan error', err instanceof ClaudeDeclined ? 'declined' : err)
    return NextResponse.json({ ok: true, mode: 'prematch', questions: [], skipped: true })
  }
}
