'use client'

import { useEffect, useRef, useState, type FormEvent } from 'react'
import { supabase, fetchPaperTrades, type PaperTrade } from './lib/supabase'

// ── Live ticker (real positions from the agent) ─────────────────────────────

function tickerLabel(t: PaperTrade): string {
  const stake = Number(t.stake_units ?? 0)
  if (t.result === 'won' && stake > 0) {
    return `WON +${Math.round(((Number(t.payout_units ?? 0) - stake) / stake) * 100)}%`
  }
  if (t.result === 'won') return 'WON'
  if (t.result === 'lost') return 'LOST'
  return 'BET PLACED'
}

function LiveTicker() {
  const [trades, setTrades] = useState<PaperTrade[]>([])

  useEffect(() => {
    fetchPaperTrades()
      .then(ts => setTrades(ts.filter(t => t.market_title).slice(0, 14)))
      .catch(() => {})
  }, [])

  return (
    <div className="ticker-bar lp-ticker" aria-hidden="true">
      <div className="ticker-inner">
        <span className="ticker-item"><b>● LIVE</b> — REAL BETS PLACED BY OUR AGENT</span>
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
    cmd: 'test "Draws are too cheap in low-scoring derbies"',
    lines: [
      { text: 'reading your idea… ok', cls: 'lp-term-dim' },
      { text: 'checking 139,241 real matches · 22 leagues · 2010-2026', cls: 'lp-term-dim' },
      { text: 'backtest   482 bets · profit +11.2% · not luck (p=0.01)', cls: 'lp-term-dim' },
      { text: 'live odds  market 28¢ vs our price 33¢ → 5¢ too cheap', cls: 'lp-term-dim' },
      { text: '✓ EDGE FOUND — READY TO BET', cls: 'lp-term-ok' },
    ],
  },
  {
    cmd: 'test "Home favorites bounce back after a heavy midweek loss"',
    lines: [
      { text: 'reading your idea… ok', cls: 'lp-term-dim' },
      { text: 'checking 139,241 real matches · form + schedule', cls: 'lp-term-dim' },
      { text: 'backtest   1,204 bets · profit +1.1% · could be luck (p=0.41)', cls: 'lp-term-dim' },
      { text: '✗ NO EDGE — IDEA REJECTED, DO NOT BET', cls: 'lp-term-warn' },
    ],
  },
  {
    cmd: 'test "NBA road favorites lose more on back-to-back nights"',
    lines: [
      { text: 'reading your idea… ok', cls: 'lp-term-dim' },
      { text: 'checking 15,415 real NBA games · rest days on', cls: 'lp-term-dim' },
      { text: 'backtest   356 bets · profit +6.8% · not luck (p=0.03)', cls: 'lp-term-dim' },
      { text: '✓ EDGE FOUND — READY TO BET', cls: 'lp-term-ok' },
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
        <span className="lp-term-title">NOPREDICTIONS — BETTING IDEA TESTER</span>
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
        <div className="lp-section-eyebrow">// BEST CALLS — REAL BETS, LOGGED BEFORE KICKOFF</div>
        <h2 className="lp-h2">The odds said no chance.<br />Our model said bet it.</h2>
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
          Same stake on every bet · each one timestamped before kickoff
        </div>
        <div className="lp-wins-honest">
          These are the three best. They are not the whole story — the agent is down
          overall right now, and every single bet it has ever placed, win or loss, is
          public. <a href="/dashboard" className="lp-body-link">See the full record →</a>
        </div>
      </div>
    </section>
  )
}

// ── Waitlist form ────────────────────────────────────────────────────────────

type FormStatus =
  | 'idle' | 'submitting' | 'success' | 'duplicate'
  | 'invalid' | 'disposable' | 'rate-limited' | 'error'

// Stricter than the browser's type="email", which happily accepts "a@b".
// Mirrors the CHECK constraint in db/027 so a bad address is caught here
// instead of coming back as a database error.
const EMAIL_RE =
  /^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$/

const DOMAIN_TYPOS: Record<string, string> = {
  'gmial.com': 'gmail.com', 'gmai.com': 'gmail.com', 'gmail.co': 'gmail.com',
  'gmail.con': 'gmail.com', 'gnail.com': 'gmail.com', 'gamil.com': 'gmail.com',
  'hotmial.com': 'hotmail.com', 'hotmai.com': 'hotmail.com', 'hotmail.co': 'hotmail.com',
  'outlok.com': 'outlook.com', 'outloo.com': 'outlook.com', 'outlook.co': 'outlook.com',
  'yahooo.com': 'yahoo.com', 'yaho.com': 'yahoo.com', 'yahoo.co': 'yahoo.com',
  'iclod.com': 'icloud.com', 'icloud.co': 'icloud.com', 'sapo.p': 'sapo.pt',
}

function typoFix(email: string): string | null {
  const at = email.lastIndexOf('@')
  if (at < 1) return null
  const fixed = DOMAIN_TYPOS[email.slice(at + 1)]
  return fixed ? `${email.slice(0, at)}@${fixed}` : null
}

const MESSAGES: Partial<Record<FormStatus, string>> = {
  invalid: "That doesn't look like an email address. Check it and try again.",
  disposable: 'Please use an inbox you actually read — we block throwaway addresses.',
  'rate-limited': "That's a few signups from this connection already. Try again later.",
  error: 'Something went wrong. Please try again.',
}

function WaitlistForm() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<FormStatus>('idle')
  const [suggestion, setSuggestion] = useState<string | null>(null)
  const honeypot = useRef<HTMLInputElement>(null)
  const shownAt = useRef(Date.now())

  async function submit(address: string) {
    setStatus('submitting')
    setSuggestion(null)

    const { error } = await supabase.from('waitlist_signups').insert({ email: address })

    if (!error) setStatus('success')
    else if (error.code === '23505') setStatus('duplicate')
    else if (error.message?.includes('waitlist_disposable_domain')) setStatus('disposable')
    else if (error.message?.includes('waitlist_rate_limited')) setStatus('rate-limited')
    else if (error.code === '23514') setStatus('invalid')
    else setStatus('error')
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (status === 'submitting') return

    // A bot fills every field it finds, including the one no human can see, and
    // submits the moment the DOM is ready. Humans do neither. Both get the
    // success screen so the bot has nothing to learn from.
    if (honeypot.current?.value || Date.now() - shownAt.current < 2500) {
      setStatus('success')
      return
    }

    const clean = email.trim().toLowerCase()
    if (!EMAIL_RE.test(clean) || clean.length < 6 || clean.length > 254 || clean.includes('..')) {
      setSuggestion(null)
      setStatus('invalid')
      return
    }

    // Offer the fix once; submitting again keeps whatever they typed.
    const fix = typoFix(clean)
    if (fix && fix !== suggestion) {
      setSuggestion(fix)
      setStatus('idle')
      return
    }

    await submit(clean)
  }

  if (status === 'success' || status === 'duplicate') {
    return (
      <div className="lp-form-success" role="status" aria-live="polite">
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
      {/* Honeypot: off-screen rather than display:none, which bots skip. */}
      <div className="lp-hp" aria-hidden="true">
        <label htmlFor="lp-company">Company — leave this empty</label>
        <input
          id="lp-company"
          name="company"
          type="text"
          ref={honeypot}
          tabIndex={-1}
          autoComplete="off"
        />
      </div>
      <div className="lp-form-row">
        <input
          type="email"
          required
          className="lp-input"
          placeholder="EMAIL ADDRESS"
          value={email}
          onChange={e => {
            setEmail(e.target.value)
            if (status === 'invalid' || status === 'disposable') setStatus('idle')
          }}
          aria-label="Email address"
          aria-invalid={status === 'invalid' || status === 'disposable'}
          autoComplete="email"
          inputMode="email"
          spellCheck={false}
          disabled={status === 'submitting'}
        />
        <button type="submit" className="lp-btn-primary lp-form-submit" disabled={status === 'submitting'}>
          {status === 'submitting' ? 'JOINING…' : 'JOIN THE WAITLIST'}
        </button>
      </div>
      <div role="status" aria-live="polite">
        {suggestion && (
          <div className="lp-form-hint">
            Did you mean{' '}
            <button
              type="button"
              className="lp-form-fix"
              onClick={() => { setEmail(suggestion); setSuggestion(null); submit(suggestion) }}
            >
              {suggestion}
            </button>
            ? Otherwise just press join again.
          </div>
        )}
        {MESSAGES[status] && <div className="lp-form-error">{MESSAGES[status]}</div>}
      </div>
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
          <a href="/dashboard" className="lp-nav-link">LIVE TRACK RECORD</a>
          <a href="#waitlist" className="lp-nav-cta">JOIN WAITLIST</a>
        </div>
      </nav>

      {/* ── LIVE TICKER ── */}
      <LiveTicker />

      {/* ── 1 · HERO ── */}
      <section className="lp-hero lp-hero-fx">
        <div className="lp-wrap lp-hero-grid">
          <div className="lp-eyebrow lp-ha-eyebrow"><span className="lp-live-dot">●</span> TEST ANY BETTING IDEA · AI AGENTS · EARLY ACCESS</div>
          <h1 className="lp-h1 lp-ha-title">
            IS YOUR<br />
            BETTING IDEA<br />
            <span className="lp-accent lp-accent-glow">PROFITABLE?</span><span className="lp-h1-cursor" />
          </h1>
          <div className="lp-ha-term">
            <AgentTerminal />
          </div>
          <p className="lp-sub lp-ha-sub">
            Write a betting idea in normal English — like &quot;draws are too cheap in
            derbies&quot;. Our AI tests it against 139,000 real matches and today&apos;s
            odds, and tells you if it makes money or loses it. If it makes money, an
            agent places the bets for you on Polymarket, 24/7.
          </p>
          <div id="waitlist" className="lp-ha-form">
            <WaitlistForm />
          </div>
          <div className="lp-note lp-ha-note">EARLY ACCESS · LIMITED SPOTS</div>
        </div>
      </section>

      {/* ── 1b · BEST CALLS (wow) ── */}
      <BestCalls />

      {/* ── 2 · HOW IT WORKS ── */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// HOW IT WORKS</div>
          <h2 className="lp-h2">Every bettor has a theory.<br />Almost nobody tests it.</h2>
          <div className="lp-steps">

            <div className="lp-step">
              <div className="lp-step-num">01</div>
              <div className="lp-step-title">SAY IT</div>
              <div className="lp-step-body">
                Type your betting idea in normal English. &quot;Home favorites collapse
                after European away games.&quot; No code. No spreadsheets.
              </div>
            </div>

            <div className="lp-step">
              <div className="lp-step-num">02</div>
              <div className="lp-step-title">PROVE IT</div>
              <div className="lp-step-body">
                The agent replays your idea over thousands of real matches and checks
                it against today&apos;s odds. Answer in seconds:{' '}
                <strong>it makes money, or it doesn&apos;t</strong>.
              </div>
            </div>

            <div className="lp-step">
              <div className="lp-step-num">03</div>
              <div className="lp-step-title">BET IT</div>
              <div className="lp-step-body">
                If it makes money, one click starts an agent that places the bets for
                you, 24/7. If it doesn&apos;t, you just saved your bankroll.
              </div>
            </div>

          </div>
        </div>
      </section>

      {/* ── 2b · QUESTIONS ── */}
      <section className="lp-section lp-section-alt">
        <div className="lp-wrap">
          <div className="lp-section-eyebrow">// STRAIGHT ANSWERS</div>
          <h2 className="lp-h2">What this actually is.</h2>
          <div className="lp-faq">

            <div className="lp-faq-item">
              <div className="lp-faq-q">So what do I get, exactly?</div>
              <div className="lp-faq-a">
                A place to check if a betting idea makes money before you risk anything
                on it. You describe the idea in one sentence; we test it on real past
                matches and real past odds, and show you the result. If it holds up, you
                can hand it to an agent that places the bets for you.
              </div>
            </div>

            <div className="lp-faq-item">
              <div className="lp-faq-q">Do I need to know maths or code?</div>
              <div className="lp-faq-a">
                No. You write one sentence in normal English. Everything else — the
                statistics, the odds, the maths — happens behind the scenes. If your idea
                is too vague to test, we tell you and help you sharpen it.
              </div>
            </div>

            <div className="lp-faq-item">
              <div className="lp-faq-q">Where do the numbers come from?</div>
              <div className="lp-faq-a">
                139,000+ real matches in football and NBA going back to 2010, and the
                closing odds from the sharpest bookmakers in the world. Ideas are tested
                only on data that existed before each match — no cheating with hindsight.
              </div>
            </div>

            <div className="lp-faq-item">
              <div className="lp-faq-q">Will this make me money?</div>
              <div className="lp-faq-a">
                We can&apos;t promise that, and anyone who does is lying to you. Most
                betting ideas turn out to be worth nothing — that is exactly the point of
                testing them. What we promise is an honest answer, fast.
              </div>
            </div>

          </div>
        </div>
      </section>

      {/* ── 3 · FOOTER CTA ── */}
      <section className="lp-section lp-section-cta">
        <div className="lp-wrap" style={{ textAlign: 'center' }}>
          <div className="lp-section-eyebrow">// EARLY ACCESS</div>
          <h2 className="lp-h2">Stop betting on vibes.</h2>
          <p className="lp-sub" style={{ marginBottom: 32 }}>
            Join the waitlist for early access — and bring the betting idea you&apos;ve
            never been able to check.
          </p>
          <div className="lp-cta-row">
            <a href="#waitlist" className="lp-btn-primary">JOIN THE WAITLIST ↑</a>
          </div>
        </div>
      </section>

      {/* ── 4 · FOOTER ── */}
      <footer className="lp-section" style={{ padding: '40px 0' }}>
        <div className="lp-wrap" style={{ textAlign: 'center' }}>
          <div className="lp-footer-links">
            <a href="/dashboard">LIVE TRACK RECORD</a>
            <a href="#waitlist">JOIN THE WAITLIST</a>
          </div>
          <div className="lp-footer-legal">
            NOPREDICTIONS.COM — AI AGENTS THAT TEST AND PLACE SPORTS BETS ON PREDICTION MARKETS
          </div>
          <div className="lp-footer-legal lp-footer-disclaimer">
            18+ ONLY · NOT FINANCIAL OR BETTING ADVICE · PAST RESULTS DO NOT PREDICT
            FUTURE RESULTS · NEVER STAKE MONEY YOU CANNOT AFFORD TO LOSE
          </div>
        </div>
      </footer>

    </div>
  )
}
