import { NextResponse } from 'next/server'
import { currentUser } from '../../../lib/supabaseAuth'
import { claimUse, refundUse, refusalMessage } from '../../../lib/plan'
import { ClaudeDeclined, askJson } from '../../../lib/claudeJson'
import { LEAGUE_CODES } from '../../../lib/backtest'
import { InplayParseSchema, cleanInplay, describeInplay } from '../../../lib/inplaySpec'
import { INPLAY_SHAPE, INPLAY_SYSTEM } from '../../../lib/inplayPrompt'
import { MAX_CLARIFIED } from '../../../lib/labQuestions'

export const maxDuration = 60

export async function POST(request: Request) {
  let theory: string
  try {
    theory = String((await request.json())?.hypothesis ?? '').trim()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid request body.' }, { status: 400 })
  }
  if (!theory || theory.length > MAX_CLARIFIED) {
    return NextResponse.json({ ok: false, error: 'That theory is too long.' }, { status: 400 })
  }

  const user = await currentUser()
  const use = await claimUse(user?.id ?? null, 'lab')
  if (!use.allowed) {
    return NextResponse.json(
      { ok: false, error: refusalMessage(use), entitlement: use },
      { status: use.reason === 'signed_out' ? 401 : 402 },
    )
  }
  const refund = () => refundUse(user?.id ?? null, 'lab')

  let parsed
  try {
    parsed = await askJson(InplayParseSchema, INPLAY_SYSTEM, `Theory: "${theory}"`, INPLAY_SHAPE)
  } catch (err) {
    console.error('inplay parse error', err instanceof ClaudeDeclined ? 'declined' : err)
    await refund()
    return NextResponse.json({ ok: false, error: 'The agent could not read this rule. Try rephrasing it.' }, { status: 502 })
  }

  if (!parsed.supported || !parsed.spec) {
    return NextResponse.json({
      ok: true,
      supported: false,
      reason: parsed.reason || 'This rule needs data the live agent does not have.',
      suggestion: parsed.suggestion || null,
      entitlement: use,
    })
  }

  const { spec, error } = cleanInplay(parsed.spec, LEAGUE_CODES)
  if (!spec) {
    return NextResponse.json({ ok: true, supported: false, reason: error, suggestion: null, entitlement: use })
  }
  const d = describeInplay(spec)
  return NextResponse.json({
    ok: true,
    supported: true,
    kind: 'inplay',
    hypothesis: theory,
    interpretation: d.summary,
    rule: d,
    spec,
    caveats: [
      ...parsed.caveats,
      'A live rule has no historical replay here: its record starts when you switch it on. Paper only, 1 unit per match, at the Polymarket ask — the sale after a goal is at the bid, and the taker fee is paid on both.',
      'Pressure comes from api-football match statistics; where a league publishes none, that match cannot fire a pressure condition.',
    ],
    entitlement: use,
  })
}
