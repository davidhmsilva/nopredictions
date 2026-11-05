'use client'

import { useEffect, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { QuotaStrip } from '../components/QuotaStrip'
import { ToolChips, ToolHead } from '../components/ToolPage'
import { useSession } from '../lib/useSession'
import { oddsText, useOddsFormat } from '../lib/display'
import { LEAGUES, NO_OU_LEAGUES } from '../lib/backtest'
import { QUOTA_RESET_TEXT } from '../lib/planTerms'
import { clarify, type LabQuestion } from '../lib/labQuestions'
import {
  ALL_LEAGUES,
  DEFAULT_PICK,
  FEATURED,
  ODDS_BANDS,
  QUICK_SIDES,
  bandText,
  type Picked,
} from '../lib/labQuick'
import { Clarify } from './Clarify'
import { InplayResult, type InplayApiResult } from './InplayResult'
import { LabResult, type LabApiResult, type Kept } from './LabResult'

// Create is the app (2026-10-28): someone describes an idea, the AI reads it,
// asks what it leaves open, writes it as a strategy with exact rules, tests it
// on 14 years of real odds where it can, and deploys it as an agent in one
// press. It was the Lab — "test a betting idea" — and the test is still here;
// it is a step on the way to an agent now, not the destination.
//
// The written idea is the part that costs a Claude call and keeps its daily
// quota. The picker and the ready-made agents run free (a cached SQL query, no
// model — see lib/labQuick), so nobody has to sign up to see what they get.

/** Ideas worth showing as a start: each needs words the picker does not have,
 *  and the last is a live rule. */
const IDEA_EXAMPLES = [
  'Back the favourite after it lost its last game',
  'Both teams to score when both have been scoring',
  'Bet against Manchester United when they have gone 3 games without a win',
  'Under 2.5 goals when both teams have conceded under a goal a game in their last 5',
  'Home teams in the Bundesliga with strong home form, unbeaten in 5',
  'Back a 1.30-1.50 favourite that is pressing while level, sell after the next goal',
]

type Mode = 'prematch' | 'inplay' | 'unsupported'

/** Where the user is in the one flow this page has. */
const STEPS = ['Describe', 'Clarify', 'Strategy', 'Deploy'] as const

/** The country, so "Premier League" and "Super League" say whose. */
// The picker offers Over/Under only where we hold its prices (db/086).
const isGoals = (side: string) => side === 'over' || side === 'under'

const LEAGUE_GROUPS = Array.from(new Set(LEAGUES.map((l) => l.country))).map((country) => ({
  country,
  leagues: LEAGUES.filter((l) => l.country === country),
}))

export default function CreatePage() {
  const [pick, setPick] = useState<Picked>(DEFAULT_PICK)
  const [input, setInput] = useState('')
  const [result, setResult] = useState<LabApiResult | null>(null)
  const [inplay, setInplay] = useState<InplayApiResult | null>(null)
  const [busy, setBusy] = useState<'quick' | 'theory' | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [gate, setGate] = useState<'signed_out' | 'quota' | null>(null)
  const [questions, setQuestions] = useState<LabQuestion[] | null>(null)
  const [asked, setAsked] = useState<{ theory: string; mode: Mode } | null>(null)
  const [kept, setKept] = useState<Kept | null>(null)
  const [lastTheory, setLastTheory] = useState('')
  const [activeExample, setActiveExample] = useState<string | null>(null)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  const fromLink = useRef(false)
  // The free build on screen, so Deploy can survive a trip through sign-up.
  const lastQuick = useRef<{ example: string } | { pick: Picked } | null>(null)
  const topRef = useRef<HTMLDivElement>(null)
  const resultRef = useRef<HTMLDivElement>(null)
  const { me, refresh } = useSession()
  const fmt = useOddsFormat()

  function reset() {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setResult(null)
    setInplay(null)
    setGate(null)
    setError(null)
    setKept(null)
    setQuestions(null)
    setStatus(null)
  }

  function showResult() {
    // Only scroll when the result is off screen — on a desktop it sits right
    // under the box and a jump would be noise.
    requestAnimationFrame(() => {
      const el = resultRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      if (r.top < 60 || r.top > window.innerHeight * 0.6) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    })
  }

  // ── the written idea (spends a build) ─────────────────────────────────────

  /** Step 1: read the idea and ask what it leaves open. The questions are a
   *  help, never a gate — any failure here goes straight to the strategy. */
  async function runTheory(theory: string) {
    if (!theory.trim() || busy) return
    reset()
    lastQuick.current = null
    setActiveExample(null)
    setBusy('theory')
    setStatus('Reading your idea…')
    showResult()
    let mode: Mode = 'prematch'
    try {
      const res = await fetch('/api/lab/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis: theory }),
      })
      if (res.status === 401 || res.status === 402) {
        refresh()
        setGate(res.status === 401 ? 'signed_out' : 'quota')
        setStatus(null)
        setBusy(null)
        return
      }
      const d = await res.json()
      if (d.ok) {
        mode = d.mode ?? 'prematch'
        if (d.questions?.length) {
          setAsked({ theory, mode })
          setQuestions(d.questions)
          setStatus(null)
          setBusy(null)
          return
        }
      }
    } catch {
      // fall through to the strategy
    }
    await proceed(mode, theory)
  }

  /** Step 2: the idea, clarified, to the engine that fits it. */
  async function proceed(mode: Mode, text: string) {
    setQuestions(null)
    setBusy('theory')
    setLastTheory(text)
    return mode === 'inplay' ? runInplay(text) : runBacktest(text)
  }

  async function runInplay(theory: string) {
    setStatus('Writing it as a live rule…')
    try {
      const res = await fetch('/api/lab/inplay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis: theory }),
      })
      const data = await res.json()
      refresh()
      if (res.status === 401 || res.status === 402) {
        setGate(res.status === 401 ? 'signed_out' : 'quota')
      } else if (!data.ok) {
        setError(data.error ?? 'Something went wrong.')
      } else if (!data.supported) {
        setResult(data)
      } else {
        setInplay(data)
      }
    } catch {
      setError('Network or server failure — nothing was spent.')
    } finally {
      setStatus(null)
      setBusy(null)
      showResult()
    }
  }

  async function runBacktest(theory: string) {
    setStatus('Writing your strategy…')
    timers.current.push(setTimeout(() => setStatus('Testing it on real games…'), 3500))
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis: theory }),
      })
      const data: LabApiResult = await res.json()
      refresh()
      if (res.status === 401 || res.status === 402) {
        setGate(res.status === 401 ? 'signed_out' : 'quota')
      } else if (!data.ok) {
        setError(data.error ?? 'Something went wrong.')
      } else {
        setResult({ ...data, title: data.title ?? theory })
      }
    } catch {
      setError('Network or server failure.')
    } finally {
      timers.current.forEach(clearTimeout)
      setStatus(null)
      setBusy(null)
      showResult()
    }
  }

  // ── the free half: a simple agent from the picker, or a ready-made one ─────

  async function runQuick(body: { example: string } | { pick: Picked }) {
    if (busy) return
    reset()
    lastQuick.current = body
    setBusy('quick')
    setActiveExample('example' in body ? body.example : null)
    try {
      const res = await fetch('/api/lab/quick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d: LabApiResult = await res.json()
      if (!d.ok) setError(d.error ?? 'Something went wrong.')
      else {
        setResult(d)
        setLastTheory(d.title ?? '')
        showResult()
      }
    } catch {
      setError('Network error — try again.')
    } finally {
      setBusy(null)
    }
  }

  // Three things can arrive in the address, each read once and then taken out
  // so a reload does not spend a second build:
  //   ?idea=     typed on the home page, or kept through sign-up: build it now
  //   ?draft=    "build a variation" of an agent: prefilled, the user edits first
  //   ?example= / ?side=&league=&odds=  a free build, back from sign-up
  useEffect(() => {
    if (fromLink.current) return
    fromLink.current = true
    try {
      const url = new URL(window.location.href)
      const q = url.searchParams
      const idea = (q.get('idea') ?? '').trim().slice(0, 500)
      const draft = (q.get('draft') ?? '').trim().slice(0, 500)
      const example = q.get('example')
      const side = q.get('side')
      if (idea || draft || example || side) window.history.replaceState(null, '', url.pathname + url.hash)
      if (idea) {
        setInput(idea)
        runTheory(idea)
      } else if (draft) {
        setInput(draft)
      } else if (example) {
        runQuick({ example })
      } else if (side) {
        const picked: Picked = {
          side: side as Picked['side'],
          league: q.get('league') ?? ALL_LEAGUES,
          odds: q.get('odds') ?? 'any',
        }
        setPick(picked)
        runQuick({ pick: picked })
      }
    } catch {
      /* no query string is the normal case */
    }
    return () => timers.current.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Keep this strategy as an agent — deployed (switched on) or as a draft.
   *  The server re-runs the backtest from the spec rather than trusting the
   *  numbers on this page — see lib/agents. */
  async function keep(deploy: boolean) {
    const spec = inplay?.spec ?? result?.spec
    if (!spec || kept?.busy) return
    if (!me?.user) {
      // Back to this same strategy after signing up, with one more press to go.
      // A written idea needed an account to build, so only an expired session
      // gets here with one: it comes back prefilled, without spending a build.
      const q = lastQuick.current
      const back = !q
        ? `/create?draft=${encodeURIComponent(lastTheory.slice(0, 500))}`
        : 'example' in q
          ? `/create?example=${encodeURIComponent(q.example)}`
          : `/create?${new URLSearchParams({ side: q.pick.side, league: q.pick.league, odds: q.pick.odds })}`
      window.location.href = `/login?mode=signup&next=${encodeURIComponent(back)}`
      return
    }
    setKept({ busy: deploy ? 'deploy' : 'draft' })
    try {
      const res = await fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hypothesis: lastTheory.slice(0, 500),
          interpretation: ((inplay ?? result)?.interpretation ?? result?.title ?? '').slice(0, 500),
          spec,
          deploy,
        }),
      })
      const d = await res.json()
      setKept(d.ok ? { id: d.id, running: d.running, note: d.note } : { error: d.error ?? 'Could not save it.' })
    } catch {
      setKept({ error: 'Network error — nothing was saved.' })
    }
  }

  function onPick(e: FormEvent) {
    e.preventDefault()
    runQuick({ pick })
  }

  function onIdea(e: FormEvent) {
    e.preventDefault()
    runTheory(input)
  }

  const showTested = !busy && result?.ok && result.supported && result.stats && result.verdict
  const showRule = !busy && inplay?.ok && inplay.supported && inplay.rule
  const step = kept?.id ? 3 : showTested || showRule ? 2 : questions ? 1 : 0
  const signupNext = `/create?idea=${encodeURIComponent((asked?.theory ?? input).slice(0, 500))}`

  return (
    <AppShell>
      <div className="tp-page lx-page" ref={topRef}>
        <ToolHead eyebrow="CREATE AN AGENT" title="Describe your idea. Get an agent that trades it.">
          Say the bet you believe in, in your own words. The AI asks what it leaves open, writes it as
          a strategy with exact rules, shows what it would have made on up to 14 years of real odds, and
          deploys it as an agent that paper-trades every game that fits.
        </ToolHead>

        <ol className="cr-steps" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li key={s} className={i === step ? 'is-now' : i < step ? 'is-done' : ''} aria-current={i === step ? 'step' : undefined}>
              <span className="cr-step-n np-num">{i + 1}</span>
              {s}
            </li>
          ))}
        </ol>

        <form className="cr-idea" onSubmit={onIdea}>
          <label htmlFor="cr-idea-box" className="cr-idea-label">
            Your idea
          </label>
          <textarea
            id="cr-idea-box"
            className="cr-idea-box"
            placeholder="e.g. Back the favourite in La Liga after it lost its last game"
            value={input}
            maxLength={500}
            rows={3}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter builds, Shift+Enter is a new line — the habit of every chat box.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                runTheory(input)
              }
            }}
            disabled={busy !== null}
          />
          <div className="cr-idea-foot">
            <QuotaStrip quota={me?.lab ?? null} signedIn={Boolean(me?.user)} feature="agent build" next="/create" />
            <button type="submit" className="tp-go cr-go" disabled={busy !== null || !input.trim()}>
              {busy === 'theory' ? 'Building…' : 'Build my agent'}
            </button>
          </div>
        </form>
        <ToolChips label="Try one">
          {IDEA_EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="tp-chip" disabled={busy !== null} onClick={() => setInput(ex)}>
              {ex}
            </button>
          ))}
        </ToolChips>

        <div className="lx-result" ref={resultRef}>
          {busy && (
            <div className="lx-busy" role="status">
              <span className="scan-spinner" />
              <span>{status ?? 'Working…'}</span>
            </div>
          )}

          {error && !busy && <div className="tp-warn lx-error">{error}</div>}

          {gate && (
            <section className="tp-gate">
              <h2>{gate === 'signed_out' ? 'Building from your own words needs a free account' : "That's today's three"}</h2>
              <p>
                {gate === 'signed_out'
                  ? 'Reading an idea and writing it as a strategy costs us a model call, so it sits behind a free account: three agents a day, no card. Your idea is kept — it will be here when you come back.'
                  : `Free accounts build three agents a day from their own words. The count resets at ${QUOTA_RESET_TEXT} — or Pro removes the limit. The ready-made agents below stay free.`}
              </p>
              <div className="tp-gate-actions">
                {gate === 'signed_out' ? (
                  <Link className="np-btn np-btn-primary" href={`/login?mode=signup&next=${encodeURIComponent(signupNext)}`}>
                    Create a free account
                  </Link>
                ) : (
                  <Link className="np-btn np-btn-primary" href="/pricing">
                    See Pro
                  </Link>
                )}
                <Link className="np-btn" href="/marketplace">
                  Copy an agent instead
                </Link>
              </div>
            </section>
          )}

          {questions && asked && !busy && (
            <Clarify
              questions={questions}
              busy={false}
              onDone={(c) => proceed(asked.mode, clarify(asked.theory, c))}
              onSkip={() => proceed(asked.mode, asked.theory)}
            />
          )}

          {showRule && <InplayResult result={inplay!} kept={kept} onKeep={keep} />}

          {!busy && result?.ok && result.supported === false && (
            <section className="lr">
              <div className="lr-title">{lastTheory}</div>
              <h2 className="lr-head">We can&apos;t build that agent yet</h2>
              <p className="lr-note">{result.reason}</p>
              {result.suggestion && (
                <div className="lr-actions">
                  <button
                    type="button"
                    className="np-btn np-btn-primary"
                    onClick={() => {
                      setInput(result.suggestion!)
                      runTheory(result.suggestion!)
                    }}
                  >
                    Build “{result.suggestion}” instead
                  </button>
                </div>
              )}
            </section>
          )}

          {showTested && (
            <LabResult
              r={result!}
              kept={kept}
              onKeep={keep}
              onTryAnother={() => topRef.current?.scrollIntoView({ behavior: 'smooth' })}
            />
          )}
        </div>

        <section className="cr-start">
          <h2>No idea yet? Start from a simple one</h2>
          <p>Pick a bet, a league and a price, or open a ready-made agent. Free, no account needed to see it.</p>

          <form className="lx-pick" onSubmit={onPick}>
            <span className="lx-pick-word">Back</span>
            <select
              className="lx-select"
              aria-label="What to back"
              value={pick.side}
              onChange={(e) => {
                const side = e.target.value as Picked['side']
                const priced = !isGoals(side) || !NO_OU_LEAGUES.has(pick.league)
                setPick({ ...pick, side, league: priced ? pick.league : ALL_LEAGUES })
              }}
            >
              {QUICK_SIDES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <span className="lx-pick-word">in</span>
            <select
              className="lx-select"
              aria-label="League"
              value={pick.league}
              onChange={(e) => setPick({ ...pick, league: e.target.value })}
            >
              <option value={ALL_LEAGUES}>Every league</option>
              {LEAGUE_GROUPS.filter((g) => !isGoals(pick.side) || !g.leagues.every((l) => NO_OU_LEAGUES.has(l.code))).map((g) => (
                <optgroup key={g.country} label={g.country}>
                  {g.leagues.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <span className="lx-pick-word">at</span>
            <select
              className="lx-select"
              aria-label="Odds"
              value={pick.odds}
              onChange={(e) => setPick({ ...pick, odds: e.target.value })}
            >
              {ODDS_BANDS.map((b) => (
                <option key={b.id} value={b.id}>
                  {bandText(b, (d) => oddsText(d, fmt)).replace(/^Any odds$/, 'any odds')}
                </option>
              ))}
            </select>
            <button type="submit" className="tp-go lx-go" disabled={busy !== null}>
              {busy === 'quick' ? 'Building…' : 'Build it'}
            </button>
          </form>

          <div className="lx-popular">
            <span className="lx-popular-label">Ready-made</span>
            {FEATURED.map((f) => (
              <button
                key={f.id}
                type="button"
                className={`tp-chip${activeExample === f.id ? ' is-on' : ''}`}
                disabled={busy !== null}
                onClick={() => runQuick({ example: f.id })}
              >
                {f.title}
              </button>
            ))}
          </div>
          <p className="lx-free">
            Or skip the building: <Link href="/marketplace">copy an agent someone already runs →</Link>
          </p>
        </section>

        <details className="lx-how">
          <summary>How an agent is built</summary>
          <ul>
            <li>
              The AI reads your idea, asks only about the choices that change what the agent would
              do, and writes the strategy as exact rules. You see the rules before anything runs.
            </li>
            <li>
              A pre-match strategy is replayed over 145,036 league matches in 33 leagues (Over/Under
              in 22 of them, where we hold its prices), 2012 to January 2026, each bet at
              Pinnacle&apos;s closing price — the sharpest price there is, and the hardest to beat.
              Both teams to score and over/under 1.5, 3.5 and 4.5 have no such close, so they are
              replayed over about a year at Polymarket&apos;s own prices, the last a taker paid before
              kick-off plus the fee, in the 21 leagues that history covers. A live rule cannot be
              replayed, so its record starts when you deploy it.
            </li>
            <li>
              A deployed agent places one paper bet of 1 unit per game that fits, at Polymarket&apos;s
              real price, and settles it on the result. Practice money, real prices: the record is
              real, the money is not.
            </li>
            <li>
              Under 200 bets we don&apos;t give a verdict: a good run looks like a pattern for a long
              time. History is the first filter; the agent&apos;s own record is the test.
            </li>
          </ul>
        </details>
      </div>
    </AppShell>
  )
}
