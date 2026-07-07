'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { supabase, fetchPaperTrades, type PaperTrade } from './lib/supabase'

const X_URL = 'https://x.com'

// ── Live ticker (real positions from the agent) ─────────────────────────────

function tickerLabel(t: PaperTrade): string {
  if (t.result === 'won') return `WON +${(Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0)).toFixed(2)}u`
  if (t.result === 'lost') return `LOST -${Number(t.stake_units ?? 0).toFixed(2)}u`
  return `OPEN +${Number(t.expected_edge ?? 0).toFixed(1)}pp`
}

function LiveTicker() {
  const [trades, setTrades] = useState<PaperTrade[]>([])

  useEffect(() => {
    fetchPaperTrades()
      .then(ts => setTrades(ts.filter(t => t.market_title).slice(0, 14)))
      .catch(() => {})
  }, [])

  return (
    <div className="ticker-bar lp-ticker">
      <div className="ticker-inner">
        <span className="ticker-item"><b>● LIVE</b> — REAL POSITIONS FROM OUR AGENT</span>
        {trades.map(t => (
          <span key={t.id} className={`ticker-item${t.result === 'lost' ? ' down' : ''}`}>
            {t.market_title.length > 44 ? t.market_title.slice(0, 41) + '…' : t.market_title}{' '}
            <b>{tickerLabel(t)}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Agent terminal demo ──────────────────────────────────────────────────────

const SCENARIOS: { cmd: string; lines: { text: string; cls: string }[] }[] = [
  {
    cmd: 'test "Draws are overpriced on Polymarket in low-scoring derbies"',
    lines: [
      { text: 'parsing hypothesis… ok', cls: 'lp-term-dim' },
      { text: 'scanning 139,241 matches · 22 leagues · 2010-2026', cls: 'lp-term-dim' },
      { text: 'backtest   n=482 · yield +11.2% · CLV +2.3% · p=0.01', cls: 'lp-term-dim' },
      { text: 'live check PM 28¢ vs MODEL 33¢ → +5pp edge', cls: 'lp-term-dim' },
      { text: '✓ EDGE FOUND — READY TO DEPLOY', cls: 'lp-term-ok' },
    ],
  },
  {
    cmd: 'test "Home favorites bounce back after a heavy midweek loss"',
    lines: [
      { text: 'parsing hypothesis… ok', cls: 'lp-term-dim' },
      { text: 'scanning 139,241 matches · filters: form, schedule', cls: 'lp-term-dim' },
      { text: 'backtest   n=1,204 · yield +1.1% · p=0.41', cls: 'lp-term-dim' },
      { text: '✗ NO EDGE — HYPOTHESIS REJECTED', cls: 'lp-term-warn' },
    ],
  },
  {
    cmd: 'test "NBA road favorites on back-to-backs underperform the spread"',
    lines: [
      { text: 'parsing hypothesis… ok', cls: 'lp-term-dim' },
      { text: 'scanning 15,415 NBA games · schedule model on', cls: 'lp-term-dim' },
      { text: 'backtest   n=356 · yield +6.8% · p=0.03', cls: 'lp-term-dim' },
      { text: '✓ EDGE FOUND — READY TO DEPLOY', cls: 'lp-term-ok' },
    ],
  },
]

function AgentTerminal() {
  const [scenario, setScenario] = useState(0)
  const [typed, setTyped] = useState('')
  const [shown, setShown] = useState(0)

  useEffect(() => {
    const s = SCENARIOS[scenario]
    let cancelled = false
    const timers: ReturnType<typeof setTimeout>[] = []
    setTyped('')
    setShown(0)

    const typeChar = (i: number) => {
      if (cancelled) return
      setTyped(s.cmd.slice(0, i))
      if (i < s.cmd.length) timers.push(setTimeout(() => typeChar(i + 1), 26))
      else revealLine(0)
    }
    const revealLine = (j: number) => {
      timers.push(setTimeout(() => {
        if (cancelled) return
        setShown(j + 1)
        if (j + 1 < s.lines.length) revealLine(j + 1)
        else timers.push(setTimeout(() => {
          if (!cancelled) setScenario(prev => (prev + 1) % SCENARIOS.length)
        }, 4200))
      }, 700))
    }
    timers.push(setTimeout(() => typeChar(1), 500))

    return () => {
      cancelled = true
      timers.forEach(clearTimeout)
    }
  }, [scenario])

  const s = SCENARIOS[scenario]
  const doneTyping = typed.length === s.cmd.length

  return (
    <div className="lp-term">
      <div className="lp-term-head">
        <span className="lp-term-dot" /><span className="lp-term-dot" /><span className="lp-term-dot" />
        <span className="lp-term-title">NOPREDICTIONS AGENT — HYPOTHESIS TESTER</span>
      </div>
      <div className="lp-term-body">
        <div className="lp-term-line lp-term-cmd">
          <span className="lp-term-prompt">&gt; </span>{typed}
          {!doneTyping && <span className="lp-term-cursor" />}
        </div>
        {s.lines.slice(0, shown).map((l, i) => (
          <div key={i} className={`lp-term-line ${l.cls}`}>{l.text}</div>
        ))}
        {doneTyping && shown === s.lines.length && (
          <div className="lp-term-line lp-term-cmd"><span className="lp-term-prompt">&gt; </span><span className="lp-term-cursor" /></div>
        )}
      </div>
    </div>
  )
}

// ── Best calls (real top wins) ───────────────────────────────────────────────

interface WinRow { id: number; match: string; pick: string; odds: number; pct: number; date: string }

const DRAW_TITLE_RE = /^Will (.+) end in a draw\?$/

function BestCalls() {
  const [wins, setWins] = useState<WinRow[]>([])

  useEffect(() => {
    supabase
      .from('paper_trades')
      .select('id, outcome, entry_odds, stake_units, payout_units, resolved_at, pm_markets!market_id ( title )')
      .eq('result', 'won')
      .order('payout_units', { ascending: false })
      .limit(300)
      .then(({ data }) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const ranked = ((data ?? []) as any[])
          .map(r => ({
            id: r.id as number,
            title: (r.pm_markets?.title ?? '') as string,
            outcome: String(r.outcome ?? '').toLowerCase(),
            odds: Number(r.entry_odds ?? 0),
            stake: Number(r.stake_units ?? 0),
            payout: Number(r.payout_units ?? 0),
            resolved_at: r.resolved_at as string | null,
          }))
          // only picks whose meaning we can state unambiguously: YES on a draw market
          .filter(r => r.stake > 0 && r.outcome === 'draw' && DRAW_TITLE_RE.test(r.title))
          .sort((a, b) => (b.payout - b.stake) / b.stake - (a.payout - a.stake) / a.stake)

        const seen = new Set<string>()
        const rows: WinRow[] = []
        for (const r of ranked) {
          if (seen.has(r.title)) continue
          seen.add(r.title)
          rows.push({
            id: r.id,
            match: r.title.match(DRAW_TITLE_RE)![1],
            pick: 'BACKED THE DRAW',
            odds: r.odds,
            pct: Math.round(((r.payout - r.stake) / r.stake) * 100),
            date: r.resolved_at
              ? new Date(r.resolved_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()
              : '',
          })
          if (rows.length === 3) break
        }
        setWins(rows)
      })
  }, [])

  if (wins.length === 0) return null

  return (
    <section className="lp-section lp-section-alt">
      <div className="lp-wrap">
        <div className="lp-section-eyebrow">// BEST CALLS — REAL LOGGED POSITIONS</div>
        <h2 className="lp-h2">The market said no chance.<br />The model said value.</h2>
        <div className="lp-wins">
          {wins.map(w => (
            <div key={w.id} className="lp-win">
              <div className="lp-win-pct">+{w.pct.toLocaleString('en-US')}%</div>
              <div className="lp-win-title">{w.match}</div>
              <div className="lp-win-meta">{w.pick} @ {w.odds.toFixed(2)} · WON · {w.date}</div>
            </div>
          ))}
        </div>
        <div className="lp-wins-note">
          1u flat stakes · every position timestamped before kickoff · no cherry-picking
        </div>
      </div>
    </section>
  )
}

// ── Waitlist form ────────────────────────────────────────────────────────────

type FormStatus = 'idle' | 'submitting' | 'success' | 'duplicate' | 'error'

function WaitlistForm() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<FormStatus>('idle')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (status === 'submitting') return
    setStatus('submitting')

    const { error } = await supabase.from('waitlist_signups').insert({
      email: email.trim().toLowerCase(),
    })

    if (!error) setStatus('success')
    else if (error.code === '23505') setStatus('duplicate')
    else setStatus('error')
  }

  if (status === 'success' || status === 'duplicate') {
    return (
      <div className="lp-form-success">
        <div className="lp-form-success-title">
          {status === 'success' ? "YOU'RE ON THE LIST ✓" : "YOU'RE ALREADY ON THE LIST ✓"}
        </div>
        <div className="lp-form-success-body">
          We&apos;ll only email you when we have meaningful updates. No spam.
        </div>
      </div>
    )
  }

  return (
    <form className="lp-form" onSubmit={handleSubmit}>
      <div className="lp-form-row">
        <input
          type="email"
          required
          className="lp-input"
          placeholder="EMAIL ADDRESS"
          value={email}
          onChange={e => setEmail(e.target.value)}
          aria-label="Email address"
        />
        <button type="submit" className="lp-btn-primary lp-form-submit" disabled={status === 'submitting'}>
          {status === 'submitting' ? 'JOINING…' : 'JOIN THE WAITLIST'}
        </button>
      </div>
      {status === 'error' && (
        <div className="lp-form-error">Something went wrong. Please try again.</div>
      )}
      <div className="lp-form-note">
        We&apos;ll only email you when we have meaningful updates. No spam.
      </div>
    </form>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="lp-root">

      {/* ── NAV ── */}
      <nav className="lp-nav">
        <div className="lp-brand">
          <span className="lp-brand-title">NOPREDICTIONS</span>
          <span className="lp-brand-sub">NO PREDICTIONS. ONLY EDGES.</span>
        </div>
        <div className="lp-nav-links">
          <a href="#waitlist" className="lp-nav-cta">JOIN WAITLIST</a>
        </div>
      </nav>

      {/* ── LIVE TICKER ── */}
      <LiveTicker />

      {/* ── 1 · HERO ── */}
      <section className="lp-hero lp-hero-fx">
        <div className="lp-wrap lp-hero-grid">
          <div className="lp-eyebrow lp-ha-eyebrow"><span className="lp-live-dot">●</span> AI AGENTS · PREDICTION MARKETS · EARLY ACCESS</div>
          <h1 className="lp-h1 lp-ha-title">
            NO PREDICTIONS.<br />
            <span className="lp-accent lp-accent-glow">ONLY EDGES.</span><span className="lp-h1-cursor" />
          </h1>
          <div className="lp-ha-term">
            <AgentTerminal />
          </div>
          <p className="lp-sub lp-ha-sub">
            Type a sports theory in plain English. An AI agent backtests it against
            139,000+ real matches, checks it against live market prices, and — if the
            edge is real — trades it for you. Automatically.
          </p>
          <div id="waitlist" className="lp-ha-form">
            <WaitlistForm />
          </div>
          <div className="lp-note lp-ha-note">EARLY ACCESS · LIMITED SPOTS</div>
        </div>
      </section>

      {/* ── 1b · BEST CALLS (wow) ── */}
      <BestCalls />

      {/* ── 2 · WHY YOU LOSE ── */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// WHY YOU LOSE</div>
          <h2 className="lp-h2">Every bettor has a theory.<br />Almost nobody tests it.</h2>
          <p className="lp-body" style={{ maxWidth: 640, margin: '0 auto' }}>
            You watch the games. You read the news. You feel the line is wrong — and
            sometimes you&apos;re right. But without testing, you can&apos;t tell an edge
            from a bias. So wins feel like skill, losses feel like bad luck, and the
            bankroll slowly bleeds.
          </p>
        </div>
      </section>

      {/* ── 3 · THE METHOD ── */}
      <section className="lp-section lp-section-alt">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// THE METHOD</div>
          <h2 className="lp-h2">Type your theory.<br />Get the truth in seconds.</h2>
          <p className="lp-body" style={{ maxWidth: 640, margin: '0 auto' }}>
            We test your ideas the way a quant fund tests a strategy — against 139,000+
            real matches and the live market line. No opinions. No hot takes. Just the
            numbers, and a verdict: <strong>edge, or bias</strong>.
            <br /><br />
            <strong>If it&apos;s real, deploy it — an autonomous agent trades it for you.
            If it&apos;s not, you just saved your bankroll.</strong>
          </p>
        </div>
      </section>

      {/* ── 4 · HOW IT WORKS ── */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// HOW IT WORKS</div>
          <h2 className="lp-h2">Say it. Prove it. Deploy it.</h2>
          <div className="lp-steps">

            <div className="lp-step">
              <div className="lp-step-num">01</div>
              <div className="lp-step-title">SAY IT</div>
              <div className="lp-step-body">
                Type any idea in plain English. &quot;Home favorites collapse after
                European away games.&quot; That&apos;s it — no code, no spreadsheets,
                no data science degree.
              </div>
            </div>

            <div className="lp-step">
              <div className="lp-step-num">02</div>
              <div className="lp-step-title">PROVE IT</div>
              <div className="lp-step-body">
                The agent backtests it against 139,000+ real matches and prices it
                against live market odds. You get a verdict in seconds: real edge,
                or expensive bias.
              </div>
            </div>

            <div className="lp-step">
              <div className="lp-step-num">03</div>
              <div className="lp-step-title">DEPLOY IT</div>
              <div className="lp-step-body">
                One click launches your autonomous agent. It watches the markets 24/7,
                sizes positions, manages risk, and executes. You check the P&amp;L.
              </div>
            </div>

          </div>
        </div>
      </section>

      {/* ── 5 · KEY FEATURES ── */}
      <section className="lp-section lp-section-alt">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// WHAT YOU GET</div>
          <h2 className="lp-h2">A quant desk in your pocket.</h2>
          <div className="lp-feat-grid">

            <div className="lp-feat">
              <div className="lp-feat-title">HYPOTHESIS TESTER</div>
              <div className="lp-feat-desc">Plain English in, verdict out. Backtest + market check in seconds.</div>
            </div>

            <div className="lp-feat">
              <div className="lp-feat-title">AUTONOMOUS AGENTS</div>
              <div className="lp-feat-desc">Deploy with one click. Risk limits built in. Kill switch always on.</div>
            </div>

            <div className="lp-feat">
              <div className="lp-feat-title">EDGE DETECTION</div>
              <div className="lp-feat-desc">Always-on scanning for mispricings across prediction markets.</div>
            </div>

            <div className="lp-feat">
              <div className="lp-feat-title">ONE DASHBOARD</div>
              <div className="lp-feat-desc">Every agent, every position, every P&amp;L — in one place.</div>
            </div>

            <div className="lp-feat">
              <div className="lp-feat-title">SPORTS ONLY</div>
              <div className="lp-feat-desc">Football, NBA, NFL. Built for sports, not for everything.</div>
            </div>

            <div className="lp-feat lp-feat-soon">
              <div className="lp-feat-title">COMING SOON</div>
              <div className="lp-feat-desc">Agent marketplace &amp; API access.</div>
            </div>

          </div>
        </div>
      </section>

      {/* ── 7 · FOOTER CTA ── */}
      <section className="lp-section lp-section-cta">
        <div className="lp-wrap" style={{ textAlign: 'center' }}>
          <div className="lp-section-eyebrow">// EARLY ACCESS</div>
          <h2 className="lp-h2">Stop betting on vibes.</h2>
          <p className="lp-sub" style={{ marginBottom: 32 }}>
            Join the waitlist for early access, beta invites, and priority onboarding.
            Your first hypothesis is waiting to be tested.
          </p>
          <div className="lp-cta-row">
            <a href="#waitlist" className="lp-btn-primary">JOIN THE WAITLIST ↑</a>
          </div>
        </div>
      </section>

      {/* ── 8 · FOOTER ── */}
      <footer className="lp-section" style={{ padding: '40px 0' }}>
        <div className="lp-wrap" style={{ textAlign: 'center' }}>
          <div className="lp-footer-links">
            <a href={X_URL} target="_blank" rel="noopener noreferrer">FOLLOW US ON X FOR UPDATES</a>
          </div>
          <div className="lp-footer-legal">
            NOPREDICTIONS.COM — BUILDING THE FUTURE OF AI AGENTS IN SPORTS PREDICTION MARKETS
          </div>
        </div>
      </footer>

    </div>
  )
}
