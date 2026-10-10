'use client'

import { useEffect, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { QuotaStrip } from '../components/QuotaStrip'
import { ToolChips, ToolForm, ToolHead } from '../components/ToolPage'
import { useSession } from '../lib/useSession'
import { oddsText, useOddsFormat } from '../lib/display'
import { LEAGUES } from '../lib/backtest'
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
import { LabResult, type LabApiResult } from './LabResult'

// The Lab used to be a headline, an empty box and a glossary: nobody saw a
// result before signing up, because every example spent a use and a use needs
// an account. Now the page opens ON a result, the picker and the popular
// tests run free (a cached SQL query, no model — see lib/labQuick), and the
// written theory, which is the part that costs a Claude call, keeps its quota.

/** Written theories worth showing as a start: each needs words the picker
 *  does not have, and the last is a live rule. */
const WRITTEN_EXAMPLES = [
  'Away underdogs on short rest in the Championship',
  'Back Barcelona at home after a bad run of form',
  'Back a 1.30-1.50 favourite that is pressing while level, sell after the next goal',
]

type Mode = 'prematch' | 'inplay' | 'unsupported'

/** The country, so "Premier League" and "Super League" say whose. */
const LEAGUE_GROUPS = Array.from(new Set(LEAGUES.map((l) => l.country))).map((country) => ({
  country,
  leagues: LEAGUES.filter((l) => l.country === country),
}))

export default function LabPage() {
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
  const [saved, setSaved] = useState<{ id?: number; error?: string; busy?: boolean } | null>(null)
  const [lastTheory, setLastTheory] = useState('')
  const [activeExample, setActiveExample] = useState<string | null>(FEATURED[0].id)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
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
    setSaved(null)
    setQuestions(null)
    setStatus(null)
  }

  function showResult() {
    // Only scroll when the result is off screen — on a desktop it sits right
    // under the picker and a jump would be noise.
    requestAnimationFrame(() => {
      const el = resultRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      if (r.top < 60 || r.top > window.innerHeight * 0.6) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    })
  }

  // ── the free half ─────────────────────────────────────────────────────────

  async function runQuick(body: { example: string } | { pick: Picked }, scroll = true) {
    if (busy) return
    reset()
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
        if (scroll) showResult()
      }
    } catch {
      setError('Network error — try again.')
    } finally {
      setBusy(null)
    }
  }

  // The page opens on a result, not on a promise of one.
  useEffect(() => {
    runQuick({ example: FEATURED[0].id }, false)
    return () => timers.current.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── the written half (spends a use) ───────────────────────────────────────

  /** Step 1: read the theory and ask what it leaves open. The questions are a
   *  help, never a gate — any failure here goes straight to the test. */
  async function runTheory(theory: string) {
    if (!theory.trim() || busy) return
    reset()
    setActiveExample(null)
    setBusy('theory')
    setStatus('Reading your theory…')
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
      // fall through to the test
    }
    await proceed(mode, theory)
  }

  /** Step 2: the theory, clarified, to the engine that fits it. */
  async function proceed(mode: Mode, text: string) {
    setQuestions(null)
    setBusy('theory')
    setLastTheory(text)
    return mode === 'inplay' ? runInplay(text) : runBacktest(text)
  }

  async function runInplay(theory: string) {
    setStatus('Turning it into a live rule…')
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
    setStatus('Turning your words into a test…')
    timers.current.push(setTimeout(() => setStatus('Replaying it over 111,475 games…'), 3500))
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

  /** Keep this test as an agent. The server re-runs the backtest from the
   *  spec rather than trusting the numbers on this page — see lib/agents. */
  async function saveAgent() {
    const spec = inplay?.spec ?? result?.spec
    if (!spec || saved?.busy) return
    if (!me?.user) {
      window.location.href = '/login?next=%2Flab'
      return
    }
    setSaved({ busy: true })
    try {
      const res = await fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hypothesis: lastTheory.slice(0, 500),
          interpretation: ((inplay ?? result)?.interpretation ?? result?.title ?? '').slice(0, 500),
          spec,
        }),
      })
      const d = await res.json()
      setSaved(d.ok ? { id: d.id } : { error: d.error ?? 'Could not save it.' })
    } catch {
      setSaved({ error: 'Network error — nothing was saved.' })
    }
  }

  function onPick(e: FormEvent) {
    e.preventDefault()
    runQuick({ pick })
  }

  function onTheory(e: FormEvent) {
    e.preventDefault()
    runTheory(input)
  }

  const showTested = !busy && result?.ok && result.supported && result.stats && result.verdict

  return (
    <AppShell>
      <div className="tp-page lx-page" ref={topRef}>
        <ToolHead eyebrow="LAB" title="Test a betting idea on 14 years of real odds.">
          Pick a bet or write your own. We replay it over 111,475 real games at the closing price and
          show you what it would have made.
        </ToolHead>

        <form className="lx-pick" onSubmit={onPick}>
          <span className="lx-pick-word">Back</span>
          <select
            className="lx-select"
            aria-label="What to back"
            value={pick.side}
            onChange={(e) => setPick({ ...pick, side: e.target.value as Picked['side'] })}
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
            {LEAGUE_GROUPS.map((g) => (
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
            {busy === 'quick' ? 'Testing…' : 'Test it'}
          </button>
        </form>

        <div className="lx-popular">
          <span className="lx-popular-label">Popular</span>
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
        <p className="lx-free">Picked and popular tests are free — no account needed.</p>

        <div className="lx-result" ref={resultRef}>
          {busy && (
            <div className="lx-busy" role="status">
              <span className="scan-spinner" />
              <span>{status ?? 'Testing…'}</span>
            </div>
          )}

          {error && !busy && <div className="tp-warn lx-error">{error}</div>}

          {gate && (
            <section className="tp-gate">
              <h2>{gate === 'signed_out' ? 'Written theories need a free account' : "That's today's three"}</h2>
              <p>
                {gate === 'signed_out'
                  ? 'Reading a theory in your own words costs us a model call, so it sits behind a free account: three a day, no card. The picker and the popular tests stay free.'
                  : `Free accounts get three written theories a day. The count resets at ${QUOTA_RESET_TEXT} — or Pro removes the limit. The picker stays free.`}
              </p>
              <div className="tp-gate-actions">
                {gate === 'signed_out' ? (
                  <Link className="np-btn np-btn-primary" href="/login?mode=signup&next=%2Flab">
                    Create a free account
                  </Link>
                ) : (
                  <Link className="np-btn np-btn-primary" href="/pricing">
                    See Pro
                  </Link>
                )}
                <button type="button" className="np-btn" onClick={() => topRef.current?.scrollIntoView({ behavior: 'smooth' })}>
                  Use the picker instead
                </button>
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

          {!busy && inplay?.ok && inplay.supported && inplay.rule && (
            <InplayResult result={inplay} saved={saved} onSave={saveAgent} />
          )}

          {!busy && result?.ok && result.supported === false && (
            <section className="lr">
              <div className="lr-title">{lastTheory}</div>
              <h2 className="lr-head">We can&apos;t test that one yet</h2>
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
                    Test “{result.suggestion}” instead
                  </button>
                </div>
              )}
            </section>
          )}

          {showTested && (
            <LabResult
              r={result!}
              saved={saved}
              onSave={saveAgent}
              onTryAnother={() => topRef.current?.scrollIntoView({ behavior: 'smooth' })}
            />
          )}
        </div>

        <section className="lx-own">
          <h2>Or write it in your own words</h2>
          <p>
            Team names, recent form, rest days, a rule for during the match — anything the picker
            can&apos;t say.
          </p>
          <QuotaStrip quota={me?.lab ?? null} signedIn={Boolean(me?.user)} feature="Lab test" next="/lab" />
          <ToolForm onSubmit={onTheory} cta={busy === 'theory' ? 'Testing…' : 'Test it'} disabled={busy !== null}>
            <input
              className="tp-input"
              placeholder="Draws are underpriced in Serie B when both teams are mid-table"
              value={input}
              maxLength={500}
              onChange={(e) => setInput(e.target.value)}
              disabled={busy !== null}
              aria-label="Your theory"
            />
          </ToolForm>
          <ToolChips label="For example">
            {WRITTEN_EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                className="tp-chip"
                disabled={busy !== null}
                onClick={() => setInput(ex)}
              >
                {ex}
              </button>
            ))}
          </ToolChips>
        </section>

        <details className="lx-how">
          <summary>How the Lab works</summary>
          <ul>
            <li>
              Football: 101,469 league matches in 22 leagues, 2012 to January 2026, each bet at
              Pinnacle&apos;s closing price — the sharpest price there is, and the hardest to beat.
              NBA: 10,006 games, 2014-15 to 2021-22, at the consensus close.
            </li>
            <li>Every bet is 1 unit, win or lose, so the profit is in units and the return is per bet.</li>
            <li>Under 200 bets we don&apos;t give a verdict: a good run looks like a pattern for a long time.</li>
            <li>
              A test over the past is the first filter, not the last. Tracking a test on new games is
              how you find out whether it holds.
            </li>
          </ul>
        </details>
      </div>
    </AppShell>
  )
}
