'use client'

import { useRef, useState, type FormEvent } from 'react'

import { isNbaMarket } from '../lib/backtest'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { QuotaStrip } from '../components/QuotaStrip'
import { ToolChips, ToolFacts, ToolForm, ToolHead, ToolOutput } from '../components/ToolPage'
import { useSession } from '../lib/useSession'

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
  caveats?: string[]
}

const EXAMPLES = [
  'Draws are underpriced in Serie B',
  'Home favorites below 1.50 are free money in the Premier League',
  'Back over 2.5 goals when both teams have been in high-scoring games',
  'Away underdogs on short rest collapse in the Championship',
  'Teams in terrible form bounce back at home in La Liga',
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

// ── what a test gives you ────────────────────────────────────────────────────
//
// Shown in place of the empty terminal. A first-time visitor has no idea what
// "backtest" buys them, and the honest answer — a number, an interval, and a
// verdict that is usually no — is more convincing than a promise.

const DATA_FACTS: { v: string; k: string }[] = [
  { v: '111,475', k: 'real games' },
  { v: '22', k: 'football leagues + NBA' },
  { v: '2012–2026', k: 'seasons covered' },
  { v: 'Pinnacle', k: 'closing odds' },
]

const OUTPUT_FACTS: { k: string; v: string }[] = [
  { k: 'Selections', v: 'How many bets your theory would actually have made. Under 200 and there is no verdict.' },
  { k: 'Yield ± 95% CI', v: 'Profit per unit staked, with the interval. The interval is the part that decides it.' },
  { k: 'p-value', v: 'The odds a result this good came from luck alone.' },
  { k: 'CLV', v: 'Whether the price moved your way after you bet. Positive yield without it is usually luck.' },
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

type Phase = 'idle' | 'running' | 'done'

export default function LabPage() {
  const [input, setInput] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [termLines, setTermLines] = useState<{ text: string; cls: string }[]>([])
  const [result, setResult] = useState<ApiResult | null>(null)
  const [gate, setGate] = useState<'signed_out' | 'quota' | null>(null)
  const [, setLastHypothesis] = useState('')
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  // The counter above the box has to move when a run spends one, without a
  // page reload — so the session is re-read after every attempt, refused ones
  // included (a refusal is how you find out the count is already zero).
  const { me, refresh } = useSession()

  function pushLine(text: string, cls = 'lp-term-dim') {
    setTermLines(prev => [...prev, { text, cls }])
  }

  async function run(hypothesis: string) {
    if (!hypothesis.trim() || phase === 'running') return
    timers.current.forEach(clearTimeout)
    timers.current = []
    setResult(null)
    setGate(null)
    setPhase('running')
    setLastHypothesis(hypothesis)
    setTermLines([{ text: `> test "${hypothesis}"`, cls: 'lp-term-cmd' }])
    timers.current.push(setTimeout(() => pushLine('parsing hypothesis…'), 400))
    timers.current.push(setTimeout(() => pushLine('translating to a testable spec…'), 2200))
    timers.current.push(
      setTimeout(
        () => pushLine('scanning 111,475 games · 22 football leagues + NBA…'),
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
          : 'dataset   football · 101,469 matches · 2012-2026 · Pinnacle close',
      )
      pushLine(
        `backtest   n=${s.n.toLocaleString('en-US')} · yield ${s.yieldPct >= 0 ? '+' : ''}${s.yieldPct.toFixed(2)}% · p=${s.pValue != null ? s.pValue.toFixed(3) : 'n/a'}${s.clvPct != null ? ` · CLV ${s.clvPct >= 0 ? '+' : ''}${s.clvPct.toFixed(2)}%` : ''}`,
      )
      const cls =
        data.verdict!.code === 'EDGE_FOUND'
          ? 'lp-term-ok'
          : data.verdict!.code === 'INSUFFICIENT_SAMPLE' || data.verdict!.code === 'NO_MATCHES'
            ? 'lp-term-warn'
            : 'lp-term-warn'
      pushLine(`${data.verdict!.code === 'EDGE_FOUND' ? '✓' : '✗'} ${data.verdict!.label}`, cls)
      setResult(data)
      setPhase('done')
    } catch {
      timers.current.forEach(clearTimeout)
      pushLine('✗ ERROR — network or server failure', 'lp-term-warn')
      setPhase('done')
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
                ? 'Replaying a theory over 111,475 games costs us a model call, so it sits behind a free account. Three a day, no card.'
                : 'Free accounts get three Lab tests a day. The count resets at 00:00 UTC — or Pro removes the limit.'}
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
                    <div className="bt-metric-v">{s.avgOdds != null ? s.avgOdds.toFixed(2) : '—'}</div>
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
          Backtests run against closing odds — Pinnacle for football, consensus for the NBA.
          Flat 1u stakes, minimum 200 selections before any verdict.{' '}
          <strong>A backtest is not an edge.</strong> It is the first filter, and most
          theories that survive it still die out of sample.
        </div>
      </div>
    </AppShell>
  )
}
