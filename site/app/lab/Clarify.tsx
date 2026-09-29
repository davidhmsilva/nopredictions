'use client'

/** The questions step: one card per open decision, the recommended option
 *  preselected, "Other" for anything the options miss. What comes back is the
 *  list of clarifications, appended to the theory by the page. */

import { useState } from 'react'
import type { LabQuestion } from '../lib/labQuestions'

export function Clarify({
  questions,
  busy,
  onDone,
  onSkip,
}: {
  questions: LabQuestion[]
  busy: boolean
  onDone: (clarifications: string[]) => void
  onSkip: () => void
}) {
  // index of the chosen option per question; -1 = "Other"
  const [pick, setPick] = useState<number[]>(() => questions.map(() => 0))
  const [other, setOther] = useState<string[]>(() => questions.map(() => ''))

  const ready = pick.every((p, i) => p >= 0 || other[i].trim().length > 1)

  function submit() {
    if (!ready || busy) return
    onDone(pick.map((p, i) => (p >= 0 ? questions[i].options[p].clarification : other[i].trim())))
  }

  return (
    <section className="bt-panel lq-panel">
      <div className="bt-label">BEFORE WE TEST IT</div>
      <p className="bt-text">
        {questions.length === 1 ? 'One decision' : `${questions.length} decisions`} change what gets tested.
        The first option in each is the one we would pick.
      </p>

      {questions.map((q, qi) => (
        <fieldset key={qi} className="lq-q" disabled={busy}>
          <legend className="lq-legend">
            <span className="lq-chip">{q.header}</span>
            <span className="lq-question">{q.question}</span>
          </legend>
          <div className="lq-options">
            {q.options.map((o, oi) => (
              <label key={oi} className={`lq-opt ${pick[qi] === oi ? 'is-on' : ''}`}>
                <input
                  type="radio"
                  name={`q${qi}`}
                  checked={pick[qi] === oi}
                  onChange={() => setPick((p) => p.map((v, j) => (j === qi ? oi : v)))}
                />
                <span className="lq-opt-text">
                  <strong>
                    {o.label}
                    {o.recommended && <em className="lq-rec">recommended</em>}
                  </strong>
                  <span>{o.description}</span>
                </span>
              </label>
            ))}
            <label className={`lq-opt lq-other ${pick[qi] === -1 ? 'is-on' : ''}`}>
              <input
                type="radio"
                name={`q${qi}`}
                checked={pick[qi] === -1}
                onChange={() => setPick((p) => p.map((v, j) => (j === qi ? -1 : v)))}
              />
              <span className="lq-opt-text">
                <strong>Other</strong>
                <input
                  className="tp-input lq-other-input"
                  placeholder="Say it in your own words"
                  value={other[qi]}
                  maxLength={120}
                  onFocus={() => setPick((p) => p.map((v, j) => (j === qi ? -1 : v)))}
                  onChange={(e) => setOther((o) => o.map((v, j) => (j === qi ? e.target.value : v)))}
                />
              </span>
            </label>
          </div>
        </fieldset>
      ))}

      <div className="bt-save">
        <button type="button" className="lp-btn-primary bt-submit" disabled={!ready || busy} onClick={submit}>
          {busy ? 'TESTING…' : 'TEST IT'}
        </button>
        <button type="button" className="tp-chip" disabled={busy} onClick={onSkip}>
          Skip — test it as written
        </button>
      </div>
    </section>
  )
}
