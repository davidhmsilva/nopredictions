'use client'

/** Agent — the empty state.
 *
 *  Nobody has an agent yet, including anyone reading this, so the page says so
 *  and explains what one would be. The record that used to live here moved to
 *  /agent/ours: it is the worked example behind the claim, not the product.
 *
 *  🔑 The CTA is honest about what it does. The Lab tests a theory over 111,475
 *     games and returns a verdict; it does not save anything and it does not
 *     start trading. So testing is named as step one and the later steps are
 *     described as what comes next — not as something waiting behind a click.
 *     This site has removed a newsletter form and demoted an alerts feature for
 *     exactly this reason; a third dead path would be a pattern.
 */

import { useEffect } from 'react'
import Link from 'next/link'
import { AppShell } from '../components/AppShell'
import { useSession } from '../lib/useSession'

/** What an agent is, in the order it would happen. */
const STEPS: { n: string; title: string; body: string; state: 'live' | 'next' }[] = [
  {
    n: '1',
    title: 'Write the theory',
    body:
      'In plain English, the way you would say it out loud. "Draws are underpriced in Serie B." The Lab turns it into a testable spec or tells you honestly that the data cannot answer it.',
    state: 'live',
  },
  {
    n: '2',
    title: 'See if it ever paid',
    body:
      'It is replayed over 111,475 real matches against Pinnacle’s closing price — the hardest version of the test. You get selections, yield with its confidence interval, a p-value, closing-line value, and the drawdown you would have had to sit through.',
    state: 'live',
  },
  {
    n: '3',
    title: 'Point it at today',
    body:
      'The theory watches the live Polymarket board and marks the fixtures it would take. Not built yet — this is where an agent stops being a backtest.',
    state: 'next',
  },
  {
    n: '4',
    title: 'Let it keep score',
    body:
      'Every position logged before the event resolves, settled against the real result, with the fee deducted. That is what makes a record worth reading. Not built yet.',
    state: 'next',
  },
]

export default function AgentPage() {
  const { me } = useSession()

  // Every URL the old dashboard published used a hash — #strategies,
  // #leaderboard, #agent — and all of them meant the record, which now lives a
  // level down. Forward rather than showing an empty state to someone who
  // followed a link to a specific section of a page that used to be here.
  //
  // ⚠️ `router.replace` with a cross-route hash does not navigate (tested: the
  //    effect runs, the URL does not move). A hard replace is the right tool
  //    anyway — this fires once, for a link published before the split, and a
  //    full load costs nothing on a path nobody takes twice.
  useEffect(() => {
    const h = window.location.hash.replace(/^#/, '')
    if (h) window.location.replace(`/agent/ours#${h}`)
  }, [])

  const signedIn = Boolean(me?.user)

  return (
    <AppShell>
      <div className="ag-empty">
        <header className="ag-empty-head">
          <span className="ag-empty-eyebrow">AGENT</span>
          <h1>You do not have an agent yet.</h1>
          <p>
            An agent is a theory that keeps working after you stop looking at it:
            it watches the board, marks the fixtures it would take, and keeps an
            honest score of what happened. Yours starts as a sentence.
          </p>
          <div className="ag-empty-cta">
            <Link href="/lab" className="np-btn np-btn-primary">
              Start in the Lab →
            </Link>
            <Link href="/agent/ours" className="np-btn">
              See the one that is running
            </Link>
          </div>
          {!signedIn && (
            <p className="ag-empty-note">
              A free account gets three Lab tests a day. No card.
            </p>
          )}
        </header>

        <section className="ag-steps">
          <div className="tp-section-head">
            <h2>How one gets built</h2>
          </div>
          <ol className="ag-steps-list">
            {STEPS.map((s) => (
              <li key={s.n} className={`ag-step is-${s.state}`}>
                <span className="ag-step-n np-num">{s.n}</span>
                <div className="ag-step-body">
                  <h3>
                    {s.title}
                    <span className={`np-badge ${s.state === 'live' ? 'is-good' : ''}`}>
                      {s.state === 'live' ? 'YOU CAN DO THIS NOW' : 'NOT BUILT YET'}
                    </span>
                  </h3>
                  <p>{s.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* ⚠️ The honest half. Steps 3 and 4 do not exist, and a page that
            walked someone through four steps without saying which two are real
            would be selling them. */}
        <div className="np-note ag-empty-honest">
          <strong>Two of those four steps are not built.</strong> Today the Lab tests a
          theory and hands you the result; nothing is saved, and nothing starts
          trading. We are not going to pretend otherwise while you are deciding
          whether this is worth your email.
          <br />
          <br />
          The one agent that does run is ours, and it is paper — simulated stakes,
          real prices, logged before each event resolved. No strategy it runs has
          reached its verdict gate, so its record is what was tried rather than
          what could have been earned. It is public anyway, losing arms included:{' '}
          <Link href="/agent/ours">see it</Link>.
        </div>
      </div>
    </AppShell>
  )
}
