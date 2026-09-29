/** The Lab's first step: before anything is tested, read the theory and ask
 *  the few questions whose answers change what gets tested.
 *
 *  Each answer is a short CLARIFICATION appended to the theory, so the step
 *  after this one reads a sentence that says what the user meant, and the
 *  existing parse (pre-match: /api/backtest; in-play: /api/lab/inplay) needs
 *  no second input format.
 *
 *  Client-safe: schema, types and the prompt (plain strings).
 */
import { z } from 'zod'
import { LEAGUES } from './backtest'

export const QuestionSchema = z.object({
  header: z.string(),          // ≤ 14 chars, a chip
  question: z.string(),
  options: z.array(
    z.object({
      label: z.string(),
      description: z.string(),
      clarification: z.string(), // appended to the theory when chosen
      recommended: z.boolean(),
    }),
  ),
})

export const PlanSchema = z.object({
  mode: z.enum(['prematch', 'inplay', 'unsupported']),
  questions: z.array(QuestionSchema),
})

export type LabQuestion = z.infer<typeof QuestionSchema>
export type LabPlan = z.infer<typeof PlanSchema>

export const MAX_QUESTIONS = 4
export const MAX_THEORY = 500
export const MAX_CLARIFIED = 1200

/** The theory the next step reads: the user's words, then each answer. */
export function clarify(theory: string, chosen: string[]): string {
  const extra = chosen.map((c) => c.trim().replace(/\.$/, '')).filter(Boolean)
  if (!extra.length) return theory
  return `${theory.trim().replace(/\.$/, '')}. ${extra.join('. ')}.`.slice(0, MAX_CLARIFIED)
}

// ── the prompt ─────────────────────────────────────────────────────────────

const LEAGUE_NAMES = LEAGUES.map((l) => `${l.name} (${l.country})`).join(', ')

export const PLAN_SYSTEM = `You are the first step of a sports-betting research tool. A user has written a theory. Before it is tested, you decide which kind of rule it is and ask the FEW questions whose answers change what gets tested — the way a sharp analyst would before writing any code.

# The two kinds of rule the tool can run

PREMATCH — decided before kick-off, replayed over ~100,000 finished football matches (2012-2026, ${LEAGUE_NAMES}) at the sharp closing price, plus the NBA 2014-2022. Markets: 1X2 (home/draw/away) and over/under 2.5 goals. Filters: odds band, season, league, favourite/underdog, a named team at home or away, rest days, recent form (points in last 5), recent goals (avg total in last 5).

INPLAY — a live rule on a match in progress, paper-traded on Polymarket football from now on (no historical replay). Buys one of: a team to win, the draw, or "one more goal". Entry conditions: which team (favourite / underdog / home / away), that team's odds at kick-off, a minute window, the score (any / level / 0-0 / team ahead / team behind), pressure (none / that team pressing / either side pressing / both — an open game), how strong ("pressing" ≈ top quarter of readings, "dominating" ≈ top tenth), pressure over the whole match so far or the last 15 minutes, the price paid, leagues. Exit: hold to the end, sell N minutes after a goal (any goal / only that team's / only the opponent's; a goal that does not stand cancels it), or sell at a set minute.

UNSUPPORTED — neither (other sports live, corners, cards, player props, half-time markets in-play, anything needing data we do not have). Ask nothing; the next step explains.

# The questions

Ask only about decisions that are (a) genuinely ambiguous in the theory AND (b) would change the rule that runs. Never ask about things with a conventional default the user would not care about (stake is always 1 unit; everything is paper). Never ask what the theory already says. If the theory is fully specified, return no questions.

For INPLAY rules always make sure these are settled — ask about each one the theory leaves open:
- the entry state (e.g. only at 0-0, any level score, or also when behind),
- the entry window (from when, until when),
- the exit (hold, sell after a goal — whose goal, how long to wait for the price to settle — or sell at a minute),
- what happens if the exit event never comes (e.g. no goal: hold to the end, or sell at a minute).
For PREMATCH rules the usual open decisions are: which leagues, what odds band, what exactly "favourite"/"in form"/"high scoring" means, which seasons.

Each question: a short header chip (≤ 14 characters), the question, and 2-4 options. Put the option you would recommend FIRST and mark it recommended=true (exactly one per question); say why in its description, without the word "recommended" (the page labels it). Each option carries a "clarification": a short phrase (≤ 90 characters) that, appended to the theory, states that decision unambiguously in plain words — e.g. "Only enter while the score is level", "Sell 5 minutes after any goal", "If there is no goal, hold to the end". At most ${MAX_QUESTIONS} questions; fewer is better.

Write the questions, options and clarifications in the SAME LANGUAGE as the theory.`

export const PLAN_SHAPE = `Reply with ONE JSON object and nothing else:
{"mode": "prematch" | "inplay" | "unsupported",
 "questions": [{"header": string, "question": string,
                "options": [{"label": string, "description": string, "clarification": string, "recommended": boolean}]}]}`
