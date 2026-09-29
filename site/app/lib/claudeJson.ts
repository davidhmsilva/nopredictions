/** Ask Claude for one JSON object and validate it against a zod schema.
 *
 *  The same shape as the match brief (lib/matchbrief.ts), for the same reason:
 *  structured outputs need Anthropic's grammar compiler, which on 2026-09-29
 *  answered every request with 503 after ~19s while plain requests answered
 *  in ~3s. So the JSON is asked for in the prompt and validated here; the
 *  structured-output call is only the second attempt, for a reply that does
 *  not parse. One SDK retry and a 25s timeout keep a route inside Vercel's
 *  60s even when both attempts run.
 *
 *  Server only.
 */
import Anthropic from '@anthropic-ai/sdk'
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod'
import type { z } from 'zod'

const MODEL = 'claude-opus-5-5'
// Opus 5.5 can decline; the fallback re-runs the same request on Opus 4.8
// inside the same call rather than leaving the Lab with nothing to show.
const FALLBACK = [{ model: 'claude-opus-4-8' }]

export class ClaudeDeclined extends Error {}

function firstObject(text: string): unknown {
  const a = text.indexOf('{')
  const b = text.lastIndexOf('}')
  if (a < 0 || b <= a) return null
  try {
    return JSON.parse(text.slice(a, b + 1))
  } catch {
    return null
  }
}

export async function askJson<S extends z.ZodTypeAny>(
  schema: S,
  system: string,
  user: string,
  shape: string,
): Promise<z.infer<S>> {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY not set')
  const client = new Anthropic({ maxRetries: 1, timeout: 25_000 })
  const base = {
    model: MODEL,
    max_tokens: 4000,
    betas: ['server-side-fallback-2026-06-01'],
    fallbacks: FALLBACK,
    messages: [{ role: 'user' as const, content: user }],
  }

  const msg = await client.beta.messages.create({
    ...base,
    output_config: { effort: 'low' },
    system: `${system}\n\n${shape}`,
  })
  if (msg.stop_reason === 'refusal') throw new ClaudeDeclined('declined')
  const text = msg.content.map((c) => (c.type === 'text' ? c.text : '')).join('')
  const first = schema.safeParse(firstObject(text))
  if (first.success) return first.data

  const again = await client.beta.messages.parse({
    ...base,
    output_config: { effort: 'low', format: betaZodOutputFormat(schema) },
    system,
  })
  if (again.stop_reason === 'refusal') throw new ClaudeDeclined('declined')
  if (again.parsed_output == null) throw new Error('empty reply')
  return again.parsed_output as z.infer<S>
}
