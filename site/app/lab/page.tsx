'use client'

import { useRef, useState, type FormEvent } from 'react'

import { isNbaMarket } from '../lib/backtest'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { QuotaStrip } from '../components/QuotaStrip'
import { ToolChips, ToolFacts, ToolForm, ToolHead, ToolOutput } from '../components/ToolPage'
import { useSession } from '../lib/useSession'
import { oddsText, useOddsFormat } from '../lib/display'
import { QUOTA_RESET_TEXT } from '../lib/planTerms'
import { clarify, type LabQuestion } from '../lib/labQuestions'
import { Clarify } from './Clarify'
import { InplayResult, type InplayApiResult } from './InplayResult'
import { LAB_FOOTBALL_MATCHES, LAB_SEASONS_TEXT, LAB_TOTAL_GAMES, labCount } from '../lib/labData'

// ── types mirrored from the API route ───────────────────────────────────────

interface Stats {
  n: number
  wins: number
  pushes?: number
  hitRatePct: number
  avgOdds: number | null
  pnl: number
  yieldPct: number
  ci95Pct: number
  pValue: number | null
  clvPct: number | null
  yieldOpenPct: number | null
  nOpen: number
  maxDrawdown: number
  firstMatch: string | null
  lastMatch: string | null
  nBetfair?: number
  firstBetfair?: string | null
}

interface Verdict {
  code: string
  label: string
  detail: string
}

interface SeasonRow {
  season: number
  n: number
  wins: number
  pnl: number
}

interface MonthRow {
  month: string
  n: number
  pnl: number
}

interface VenueArm {
  n: number
  wins: number
  avgOdds: number | null
  yieldPct: number
  ci95Pct: number
  pValue: number | null
  pinYieldPct: number | null
  pinAvgOdds: number | null
}

interface VenueRow {
  venue: string
  name: string
  listed: number
  paid: VenueArm | null
  mid: VenueArm | null
  agreePct: number | null
  compared: number
  dropped: number
  firstMatch: string | null
  lastMatch: string | null
}

interface ApiResult {
  ok: boolean
  error?: string
  supported?: boolean
  reason?: string
  suggestion?: string | null
  interpretation?: string | null
  spec?: { market?: string }
  verdict?: Verdict
  stats?: Stats
  seasons?: SeasonRow[]
  monthly?: MonthRow[]
  venues?: VenueRow[]
  caveats?: string[]
}

const EXAMPLES = [
  'Draws are underpriced in Serie B',
  'Home favorites below 1.50 are free money in the Premier League',
  'Back over 2.5 goals when both teams have been in high-scoring games',
  'Away underdogs on short rest collapse in the Championship',
  'Teams in terrible form bounce back at home in La Liga',
  'Back a 1.30-1.50 favourite that is pressing while level, sell after the next goal',
]

// ── equity curve ─────────────────────────────────────────────────────────────

function EquityCurve({ monthly }: { monthly: MonthRow[] }) {
  if (monthly.length < 2) return null
  const W = 640
  const H = 180
  const PAD = 8
  let cum = 0
  const pts = monthly.map(m => (cum += m.pnl))
  const min = Math.min(0, ...pts)
  const max = Math.max(0, ...pts)
  const range = max - min || 1
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => PAD + (1 - (v - min) / range) * (H - 2 * PAD)
  const path = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const zeroY = y(0)
  const final = pts[pts.length - 1]

  return (
    <div className="bt-chart">
      <div className="bt-chart-head">
        <span>CUMULATIVE P&amp;L (UNITS, 1U FLAT)</span>
        <span className={final >= 0 ? 'bt-pos' : 'bt-neg'}>
          {final >= 0 ? '+' : ''}
          {final.toFixed(1)}u
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="bt-chart-svg" preserveAspectRatio="none">
        <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} className="bt-chart-zero" />
        <path d={path} className={final >= 0 ? 'bt-chart-line bt-chart-line-pos' : 'bt-chart-line bt-chart-line-neg'} />
      </svg>
      <div className="bt-chart-foot">
        <span>{monthly[0].month}</span>
        <span>{monthly[monthly.length - 1].month}</span>
      </div>
    </div>
  )
}

// ── the same bets at the exchanges ───────────────────────────────────────────
//
// The verdict above is the sharp close's (Pinnacle, else Betfair), over
// every season. This is the question a prediction-market user brings: those
// bets, on the games Polymarket and Kalshi listed, at the price a taker would
// actually have paid there. The last column is the SAME games at the sharp
// close, because the exchanges only start in 2024-25 and a yield over twelve
// seasons is not comparable with one over two.

function pct(v: number | null, digits = 1) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function VenuePanel({ venues, oddsFmt }: { venues: VenueRow[]; oddsFmt: ReturnType<typeof useOddsFormat> }) {
  if (venues.length === 0) return null
  const thin = venues.some(v => (v.paid?.n ?? 0) < 200)
  return (
    <div className="bt-venues">
      <div className="bt-label">THE SAME BETS AT THE EXCHANGES</div>
      <p className="bt-text">
        Every selection above that Polymarket or Kalshi also listed, priced where a
        trader could actually have bought it, fee included. The last column is those
        same games at the sharp close — Pinnacle&rsquo;s, or Betfair&rsquo;s where Pinnacle is
        gone — so the two numbers are comparable.
      </p>
      <table className="bt-table bt-venue-table">
        <thead>
          <tr>
            <th>EXCHANGE</th>
            <th>BETS</th>
            <th>AVG ODDS</th>
            <th>YIELD ± 95% CI</th>
            <th>AT THE MID</th>
            <th>SHARP CLOSE, SAME GAMES</th>
          </tr>
        </thead>
        <tbody>
          {venues.map(v => (
            <tr key={v.venue}>
              <td>{v.name}</td>
              <td>
                {(v.paid?.n ?? 0).toLocaleString('en-US')}
                <span className="bt-venue-sub"> of {v.listed.toLocaleString('en-US')} listed</span>
              </td>
              <td>{oddsText(v.paid?.avgOdds ?? null, oddsFmt)}</td>
              <td className={(v.paid?.yieldPct ?? 0) >= 0 ? 'bt-pos' : 'bt-neg'}>
                {v.paid ? `${pct(v.paid.yieldPct)} ± ${v.paid.ci95Pct.toFixed(1)}` : '—'}
              </td>
              <td>{pct(v.mid?.yieldPct ?? null)}</td>
              <td>{pct(v.paid?.pinYieldPct ?? null)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="bt-venue-notes">
        <li>
          <strong>Paid</strong> is the ask at kick-off on Kalshi, and the last price a taker
          actually paid in the hour before kick-off on Polymarket, plus that
          exchange&rsquo;s fee at the time. Bets with no such price are left out, which is
          why a game can be listed and not counted.
        </li>
        <li>
          <strong>At the mid</strong> is the middle of the book. Nobody buying gets it, so
          read it as a ceiling, not a result.
        </li>
        {venues.map(v =>
          v.agreePct != null ? (
            <li key={`a-${v.venue}`}>
              {v.name} settled {v.agreePct.toFixed(1)}% of {v.compared.toLocaleString('en-US')}{' '}
              games the way our results say
              {v.firstMatch && v.lastMatch
                ? `, ${v.firstMatch.slice(0, 7)} to ${v.lastMatch.slice(0, 7)}`
                : ''}
              .
            </li>
          ) : null,
        )}
        {thin && (
          <li>
            Under 200 bets on an exchange is descriptive only, the same rule as the verdict
            above.
          </li>
        )}
      </ul>
    </div>
  )
}

// ── what a test gives you ────────────────────────────────────────────────────
//
// Shown in place of the empty terminal. A first-time visitor has no idea what
// "backtest" buys them, and the honest answer — a number, an interval, and a
// verdict that is usually no — is more convincing than a promise.

const DATA_FACTS: { v: string; k: string }[] = [
  { v: labCount(LAB_TOTAL_GAMES), k: 'real games' },
  { v: '22', k: 'football leagues + NBA' },
  { v: LAB_SEASONS_TEXT, k: 'seasons covered' },
  { v: 'Pinnacle + Betfair', k: 'closing odds' },
]

const OUTPUT_FACTS: { k: string; v: string }[] = [
  { k: 'Selections', v: 'How many bets your theory would actually have made. Under 200 and there is no verdict.' },
  { k: 'Yield ± 95% CI', v: 'Profit per unit staked, with the interval. The interval is the part that decides it.' },
  { k: 'p-value', v: 'The odds a result this good came from luck alone.' },
  { k: 'CLV', v: 'Whether the price moved your way after you bet. Positive yield without it is usually luck.' },
  { k: 'Exchange price', v: 'The same bets at Polymarket and Kalshi, on the games they listed, at what a taker paid there.' },
  { k: 'Equity curve', v: 'The run of it — including the drawdown you would have had to sit through.' },
]

function WhatYouGet() {
  return (
    <section className="tp-explain">
      <ToolFacts facts={DATA_FACTS} />
      <ToolOutput rows={OUTPUT_FACTS} />
    </section>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

type Phase = 'idle' | 'running' | 'asking' | 'done'
type Mode = 'prematch' | 'inplay' | 'unsupported'

export default function LabPage() {
  const [input, setInput] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [termLines, setTermLines] = useState<{ text: string; cls: string }[]>([])
  const [result, setResult] = useState<ApiResult | null>(null)
  const [gate, setGate] = useState<'signed_out' | 'quota' | null>(null)
  const [lastHypothesis, setLastHypothesis] = useState('')
  const [saved, setSaved] = useState<{ id?: number; error?: string; busy?: boolean } | null>(null)
  // The questions step: what was asked, of which theory, and for which kind
  // of rule. A live rule has its own result, with no backtest in it.
  const [questions, setQuestions] = useState<LabQuestion[] | null>(null)
  const [asked, setAsked] = useState<{ theory: string; mode: Mode } | null>(null)
  const [inplay, setInplay] = useState<InplayApiResult | null>(null)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  // The counter above the box has to move when a run spends one, without a
  // page reload — so the session is re-read after every attempt, refused ones
  // included (a refusal is how you find out the count is already zero).
  const { me, refresh } = useSession()
  const oddsFmt = useOddsFormat()

  function pushLine(text: string, cls = 'lp-term-dim') {
    setTermLines(prev => [...prev, { text, cls }])
  }

  /** Step 1: read the theory and ask what it leaves open. The questions are a
   *  help, never a gate — any failure here goes straight to the test. */
  async function run(hypothesis: string) {
    if (!hypothesis.trim() || phase === 'running') return
    timers.current.forEach(clearTimeout)
    timers.current = []
    setResult(null)
    setInplay(null)
    setGate(null)
    setSaved(null)
    setQuestions(null)
    setPhase('running')
    setTermLines([
      { text: `> read "${hypothesis}"`, cls: 'lp-term-cmd' },
      { text: 'checking what the theory leaves open…', cls: 'lp-term-dim' },
    ])
    let mode: Mode = 'prematch'
    try {
      const res = await fetch('/api/lab/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis }),
      })
      if (res.status === 401 || res.status === 402) {
        refresh()
        setGate(res.status === 401 ? 'signed_out' : 'quota')
        setTermLines([])
        setPhase('done')
        return
      }
      const d = await res.json()
      if (d.ok) {
        mode = d.mode ?? 'prematch'
        if (d.questions?.length) {
          pushLine(
            `${mode === 'inplay' ? 'live rule' : 'pre-match theory'} · ${d.questions.length} question${d.questions.length === 1 ? '' : 's'} before it runs`,
            'lp-term-ok',
          )
          setAsked({ theory: hypothesis, mode })
          setQuestions(d.questions)
          setPhase('asking')
          return
        }
      }
    } catch {
      // fall through to the test
    }
    await proceed(mode, hypothesis)
  }

  /** Step 2: the theory, clarified, to the engine that fits it. */
  async function proceed(mode: Mode, text: string) {
    setQuestions(null)
    if (mode === 'inplay') return runInplay(text)
    return runBacktest(text)
  }

  async function runInplay(hypothesis: string) {
    setPhase('running')
    setLastHypothesis(hypothesis)
    setTermLines([
      { text: `> build "${hypothesis}"`, cls: 'lp-term-cmd' },
      { text: 'translating to a live rule…', cls: 'lp-term-dim' },
    ])
    try {
      const res = await fetch('/api/lab/inplay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis }),
      })
      const data = await res.json()
      refresh()
      if (res.status === 401 || res.status === 402) {
        setGate(res.status === 401 ? 'signed_out' : 'quota')
        setTermLines([])
        setPhase('done')
        return
      }
      if (!data.ok) {
        pushLine(`✗ ERROR — ${data.error ?? 'something went wrong'}`, 'lp-term-warn')
        setPhase('done')
        return
      }
      if (!data.supported) {
        pushLine('✗ NOT EXPRESSIBLE AS A LIVE RULE YET', 'lp-term-warn')
        setResult(data)
        setPhase('done')
        return
      }
      pushLine('rule      live · Polymarket football · paper, 1u per match', 'lp-term-dim')
      pushLine('✓ READY TO RUN — the forward record is the test', 'lp-term-ok')
      setInplay(data)
      setPhase('done')
    } catch {
      pushLine('✗ ERROR — network or server failure', 'lp-term-warn')
      setPhase('done')
    }
  }

  async function runBacktest(hypothesis: string) {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setResult(null)
    setInplay(null)
    setGate(null)
    setSaved(null)
    setPhase('running')
    setLastHypothesis(hypothesis)
    setTermLines([{ text: `> test "${hypothesis}"`, cls: 'lp-term-cmd' }])
    timers.current.push(setTimeout(() => pushLine('parsing hypothesis…'), 400))
    timers.current.push(setTimeout(() => pushLine('translating to a testable spec…'), 2200))
    timers.current.push(
      setTimeout(
        () => pushLine(`scanning ${labCount(LAB_TOTAL_GAMES)} games · 22 football leagues + NBA…`),
        5200,
      ),
    )

    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hypothesis }),
      })
      const data: ApiResult = await res.json()
      timers.current.forEach(clearTimeout)
      refresh()

      // 401 = no account, 402 = the day is spent. Neither is an error, and
      // printing "✗ ERROR" over a sign-in prompt tells someone their theory
      // broke something when the only thing that happened is that they are
      // not signed in.
      if (res.status === 401 || res.status === 402) {
        setGate(res.status === 401 ? 'signed_out' : 'quota')
        setTermLines([])
        setPhase('done')
        return
      }

      if (!data.ok) {
        pushLine(`✗ ERROR — ${data.error ?? 'something went wrong'}`, 'lp-term-warn')
        setPhase('done')
        return
      }
      if (!data.supported) {
        pushLine('✗ NOT TESTABLE WITH CURRENT DATA', 'lp-term-warn')
        setResult(data)
        setPhase('done')
        return
      }
      const s = data.stats!
      // name the dataset that actually ran — the pre-flight line is a guess
      pushLine(
        isNbaMarket(data.spec?.market ?? '')
          ? 'dataset   NBA · 10,006 games · 2014-15 → 2021-22 · consensus close'
          : `dataset   football · ${labCount(LAB_FOOTBALL_MATCHES)} matches · ${LAB_SEASONS_TEXT} · sharp close (Pinnacle, Betfair where it is missing)`,
      )
      if ((s.nBetfair ?? 0) > 0) {
        pushLine(
          `priced    ${labCount(s.nBetfair!)} of ${labCount(s.n)} at the Betfair close, net of 5% commission — no Pinnacle price for those games`,
        )
      }
      pushLine(
        `backtest   n=${s.n.toLocaleString('en-US')} · yield ${s.yieldPct >= 0 ? '+' : ''}${s.yieldPct.toFixed(2)}% · p=${s.pValue != null ? s.pValue.toFixed(3) : 'n/a'}${s.clvPct != null ? ` · CLV ${s.clvPct >= 0 ? '+' : ''}${s.clvPct.toFixed(2)}%` : ''}`,
      )
      const cls =
        data.verdict!.code === 'EDGE_FOUND'
          ? 'lp-term-ok'
          : data.verdict!.code === 'INSUFFICIENT_SAMPLE' || data.verdict!.code === 'NO_MATCHES'
            ? 'lp-term-warn'
            : 'lp-term-warn'
      for (const v of data.venues ?? []) {
        if (!v.paid) continue
        pushLine(
          `${v.venue.padEnd(10)} n=${v.paid.n.toLocaleString('en-US')} of ${v.listed.toLocaleString('en-US')} listed · yield ${v.paid.yieldPct >= 0 ? '+' : ''}${v.paid.yieldPct.toFixed(2)}% at the price paid${v.paid.pinYieldPct != null ? ` · Pinnacle same games ${v.paid.pinYieldPct >= 0 ? '+' : ''}${v.paid.pinYieldPct.toFixed(2)}%` : ''}`,
        )
      }
      pushLine(`${data.verdict!.code === 'EDGE_FOUND' ? '✓' : '✗'} ${data.verdict!.label}`, cls)
      setResult(data)
      setPhase('done')
    } catch {
      timers.current.forEach(clearTimeout)
      pushLine('✗ ERROR — network or server failure', 'lp-term-warn')
      setPhase('done')
    }
  }

  /** Keep this theory as an agent. The server re-runs the backtest from the
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
          hypothesis: lastHypothesis,
          interpretation: (inplay ?? result)?.interpretation ?? '',
          spec,
        }),
      })
      const d = await res.json()
      setSaved(d.ok ? { id: d.id } : { error: d.error ?? 'Could not save it.' })
    } catch {
      setSaved({ error: 'Network error — nothing was saved.' })
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    run(input)
  }

  const s = result?.stats
  const showResults = phase === 'done' && result?.ok && result.supported && s

  return (
    <AppShell>
      <div className="tp-page">
        <ToolHead eyebrow="LAB" title="You have a theory. Find out if it pays.">
          Write it the way you would say it out loud. We replay it over every game we
          have and tell you what it would have made — including when the answer is
          nothing, which it usually is.
        </ToolHead>

        <QuotaStrip
          quota={me?.lab ?? null}
          signedIn={Boolean(me?.user)}
          feature="Lab test"
          next="/lab"
        />

        <ToolForm onSubmit={handleSubmit} cta={phase === 'running' ? 'TESTING…' : 'TEST IT'} disabled={phase === 'running'}>
          <input
            className="tp-input"
            placeholder='e.g. "Draws are underpriced in Serie B"'
            value={input}
            maxLength={500}
            onChange={e => setInput(e.target.value)}
            disabled={phase === 'running'}
            aria-label="Hypothesis"
          />
        </ToolForm>

        <ToolChips label="TRY ONE">
          {EXAMPLES.map(ex => (
            <button
              key={ex}
              type="button"
              className="tp-chip"
              disabled={phase === 'running'}
              onClick={() => {
                setInput(ex)
                run(ex)
              }}
            >
              {ex}
            </button>
          ))}
        </ToolChips>


        {/* The terminal was the first thing on the page and, until you ran
            something, it was an empty grey box the height of a screen. It now
            appears when there is something in it; before that the space says
            what comes back instead. */}
        {gate && (
          <section className="tp-gate">
            <h2>{gate === 'signed_out' ? 'This one needs an account' : "That is today's three"}</h2>
            <p>
              {gate === 'signed_out'
                ? `Replaying a theory over ${labCount(LAB_TOTAL_GAMES)} games costs us a model call, so it sits behind a free account. Three a day, no card.`
                : `Free accounts get three Lab tests a day. The count resets at ${QUOTA_RESET_TEXT} — or Pro removes the limit.`}
            </p>
            <div className="tp-gate-actions">
              {gate === 'signed_out' ? (
                <Link className="np-btn np-btn-primary" href="/login?next=%2Flab">
                  Sign in — it is free
                </Link>
              ) : (
                <Link className="np-btn np-btn-primary" href="/pricing">See Pro</Link>
              )}
              <Link className="np-btn" href="/insights">Read what we already tested</Link>
            </div>
          </section>
        )}

        {termLines.length > 0 ? (
          <div className="lp-term bt-term">
            <div className="lp-term-head">
              <span className="lp-term-dot" />
              <span className="lp-term-dot" />
              <span className="lp-term-dot" />
              <span className="lp-term-title">NOPREDICTIONS AGENT — HYPOTHESIS TESTER</span>
            </div>
            <div className="lp-term-body bt-term-body">
              {termLines.map((l, i) => (
                <div key={i} className={`lp-term-line ${l.cls}`}>
                  {l.text}
                </div>
              ))}
              {phase === 'running' && (
                <div className="lp-term-line lp-term-cmd">
                  <span className="lp-term-cursor" />
                </div>
              )}
            </div>
          </div>
        ) : (
          <WhatYouGet />
        )}

        {phase === 'asking' && questions && asked && (
          <Clarify
            questions={questions}
            busy={false}
            onDone={(c) => proceed(asked.mode, clarify(asked.theory, c))}
            onSkip={() => proceed(asked.mode, asked.theory)}
          />
        )}

        {phase === 'done' && inplay?.ok && inplay.supported && inplay.rule && (
          <InplayResult result={inplay} saved={saved} onSave={saveAgent} />
        )}

        {/* not testable */}
        {phase === 'done' && result?.ok && result.supported === false && (
          <section className="bt-panel">
            <div className="bt-verdict bt-verdict-warn">NOT TESTABLE — YET</div>
            <p className="bt-text">{result.reason}</p>
            {result.suggestion && (
              <div className="bt-suggestion">
                <div className="bt-label">CLOSEST TESTABLE THEORY</div>
                <p className="bt-text">&ldquo;{result.suggestion}&rdquo;</p>
                <button
                  type="button"
                  className="lp-btn-primary bt-submit"
                  onClick={() => {
                    setInput(result.suggestion!)
                    run(result.suggestion!)
                  }}
                >
                  TEST THAT INSTEAD
                </button>
              </div>
            )}
          </section>
        )}

        {/* results */}
        {showResults && (
          <section className="bt-panel">
            <div
              className={`bt-verdict ${
                result!.verdict!.code === 'EDGE_FOUND'
                  ? 'bt-verdict-ok'
                  : result!.verdict!.code === 'INSUFFICIENT_SAMPLE' || result!.verdict!.code === 'NO_MATCHES'
                    ? 'bt-verdict-warn'
                    : 'bt-verdict-bad'
              }`}
            >
              {result!.verdict!.label}
            </div>
            <p className="bt-text">{result!.verdict!.detail}</p>

            {result!.interpretation && (
              <>
                <div className="bt-label">WHAT WAS ACTUALLY TESTED</div>
                <p className="bt-text bt-interp">{result!.interpretation}</p>
              </>
            )}

            <div className="bt-save">
              {saved?.id ? (
                <p className="bt-text">
                  Saved. <Link href={`/agent/${saved.id}`}>Open the agent</Link> and press Run it —
                  it trades nothing until you do.
                </p>
              ) : (
                <>
                  <button
                    type="button"
                    className="lp-btn-primary bt-submit"
                    disabled={saved?.busy}
                    onClick={saveAgent}
                  >
                    {saved?.busy ? 'SAVING…' : 'SAVE AS AN AGENT'}
                  </button>
                  <span className="bt-save-note">
                    Whatever the verdict above. Switch it on in Agents and it paper-trades the
                    next games that fit — the forward record is the test history cannot run.
                  </span>
                  {saved?.error && <p className="bt-text bt-neg">{saved.error}</p>}
                </>
              )}
            </div>

            {s.n > 0 && (
              <>
                <div className="bt-metrics">
                  <div className="bt-metric">
                    <div className="bt-metric-v">{s.n.toLocaleString('en-US')}</div>
                    <div className="bt-metric-k">SELECTIONS</div>
                  </div>
                  <div className="bt-metric">
                    <div className="bt-metric-v">{s.hitRatePct.toFixed(1)}%</div>
                    <div className="bt-metric-k">HIT RATE</div>
                  </div>
                  <div className="bt-metric">
                    <div className="bt-metric-v">{oddsText(s.avgOdds, oddsFmt)}</div>
                    <div className="bt-metric-k">AVG ODDS</div>
                  </div>
                  <div className="bt-metric">
                    <div className={`bt-metric-v ${s.pnl >= 0 ? 'bt-pos' : 'bt-neg'}`}>
                      {s.pnl >= 0 ? '+' : ''}
                      {s.pnl.toFixed(1)}u
                    </div>
                    <div className="bt-metric-k">TOTAL P&amp;L</div>
                  </div>
                  <div className="bt-metric">
                    <div className={`bt-metric-v ${s.yieldPct >= 0 ? 'bt-pos' : 'bt-neg'}`}>
                      {s.yieldPct >= 0 ? '+' : ''}
                      {s.yieldPct.toFixed(2)}%
                    </div>
                    <div className="bt-metric-k">YIELD ± {s.ci95Pct.toFixed(2)} (95% CI)</div>
                  </div>
                  <div className="bt-metric">
                    <div className="bt-metric-v">{s.pValue != null ? s.pValue.toFixed(3) : '—'}</div>
                    <div className="bt-metric-k">P-VALUE VS ZERO</div>
                  </div>
                  <div className="bt-metric">
                    <div className={`bt-metric-v ${(s.clvPct ?? 0) >= 0 ? 'bt-pos' : 'bt-neg'}`}>
                      {s.clvPct != null ? `${s.clvPct >= 0 ? '+' : ''}${s.clvPct.toFixed(2)}%` : '—'}
                    </div>
                    <div className="bt-metric-k">AVG CLV (OPEN→CLOSE)</div>
                  </div>
                  <div className="bt-metric">
                    <div className="bt-metric-v bt-neg">-{s.maxDrawdown.toFixed(1)}u</div>
                    <div className="bt-metric-k">MAX DRAWDOWN</div>
                  </div>
                </div>

                {/* Rule 5 of this project's methodology: positive yield with
                    non-positive CLV is luck until proven otherwise. The verdict
                    above is computed from yield and p-value alone, so when the
                    two arms disagree the page has to say so rather than let
                    "EDGE FOUND" stand on its own. */}
                {s.yieldPct > 0 && s.clvPct != null && s.clvPct <= 0 && (
                  <div className="np-note bt-clv-warn">
                    <strong>The two arms disagree.</strong> This made money at the closing
                    price, but its CLV is{' '}
                    <span className="np-num">{s.clvPct.toFixed(2)}%</span> — the odds did not
                    move from open to close, so nothing says the market was wrong here rather
                    than the sample being kind. Positive yield with non-positive CLV is the
                    signature of luck, and it is the first thing to check out of sample.
                  </div>
                )}

                <VenuePanel venues={result!.venues ?? []} oddsFmt={oddsFmt} />

                <EquityCurve monthly={result!.monthly ?? []} />

                {(result!.seasons?.length ?? 0) > 1 && (
                  <div className="bt-seasons">
                    <div className="bt-label">BY SEASON</div>
                    <table className="bt-table">
                      <thead>
                        <tr>
                          <th>SEASON</th>
                          <th>N</th>
                          <th>HIT</th>
                          <th>P&amp;L</th>
                          <th>YIELD</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result!.seasons!.map(r => (
                          <tr key={r.season}>
                            <td>
                              {r.season}-{String((r.season + 1) % 100).padStart(2, '0')}
                            </td>
                            <td>{r.n.toLocaleString('en-US')}</td>
                            <td>{((r.wins / r.n) * 100).toFixed(0)}%</td>
                            <td className={r.pnl >= 0 ? 'bt-pos' : 'bt-neg'}>
                              {r.pnl >= 0 ? '+' : ''}
                              {r.pnl.toFixed(1)}u
                            </td>
                            <td className={r.pnl >= 0 ? 'bt-pos' : 'bt-neg'}>
                              {((r.pnl / r.n) * 100).toFixed(1)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}

            {(result!.caveats?.length ?? 0) > 0 && (
              <div className="bt-caveats">
                <div className="bt-label">HONESTY NOTES</div>
                <ul>
                  {result!.caveats!.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        <div className="np-note bt-foot">
          Backtests run against closing odds — Pinnacle for football (the Betfair Exchange
          close, net of commission, where Pinnacle is missing: our source stopped publishing
          it during 2025-26), consensus for the NBA.
          Flat 1u stakes, minimum 200 selections before any verdict.{' '}
          <strong>A backtest is not an edge.</strong> It is the first filter, and most
          theories that survive it still die out of sample.
        </div>
      </div>
    </AppShell>
  )
}
