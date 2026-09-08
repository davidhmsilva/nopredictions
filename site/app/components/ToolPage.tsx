'use client'

/** The shared frame for a tool page — the Lab and the Wallet analyser.
 *
 *  Both do the same job: a headline, a box you paste something into, some
 *  shortcuts, and an honest list of what comes back. They were built at
 *  different times and shared nothing — `np-page-head` / `np-h1` / `bt-form` /
 *  `bt-chip` on one, `scanner-hero` / `scanner-h1` / `analyze-input-row` /
 *  `wallet-known-chip` on the other, with the Wallet not even using AppShell.
 *  The result was two pages of visibly different scale sitting one tab apart:
 *  the Lab's heading was two thirds the size of the Wallet's, its eyebrow grey
 *  where the other was green, its container a different width.
 *
 *  🔑 Making them consistent by CONSTRUCTION rather than by matching numbers in
 *     two stylesheets is the point. The next tool page gets it for free, and
 *     the two cannot drift apart again.
 *
 *  Scale and colour follow the Wallet (the bigger heading, the green eyebrow),
 *  and the section headings follow Dropping Odds — the green left rule — so
 *  all three tabs read as one product.
 */

import type { ReactNode } from 'react'

export interface Fact {
  /** The number or word that carries it. */
  v: string
  /** What it is. */
  k: string
}

export interface OutputRow {
  k: string
  v: string
}

export function ToolHead({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string
  title: ReactNode
  children: ReactNode
}) {
  return (
    <header className="tp-head">
      <span className="tp-eyebrow">{eyebrow}</span>
      <h1 className="tp-h1">{title}</h1>
      <p className="tp-sub">{children}</p>
    </header>
  )
}

/** The paste box and its button. One shape, so the Lab's loud filled green and
 *  the Wallet's quieter outline stop being two different ideas about what a
 *  primary action looks like. */
export function ToolForm({
  onSubmit,
  children,
  cta,
  disabled,
}: {
  onSubmit: (e: React.FormEvent) => void
  /** The input itself, so each page keeps its own validation and placeholder. */
  children: ReactNode
  cta: string
  disabled?: boolean
}) {
  return (
    <form className="tp-form" onSubmit={onSubmit}>
      {children}
      <button type="submit" className="tp-go" disabled={disabled}>
        {cta}
      </button>
    </form>
  )
}

/** Shortcuts under the box. Two shapes, because the two pages have genuinely
 *  different content: the Lab's are whole sentences to try, the Wallet's are
 *  named wallets with a one-line reason. A single shape would have squeezed
 *  one of them. */
export function ToolChips({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="tp-chips">
      {label && <div className="tp-chips-label">{label}</div>}
      <div className="tp-chips-row">{children}</div>
    </div>
  )
}

export function ToolFacts({ facts }: { facts: Fact[] }) {
  return (
    <div className="tp-facts">
      {facts.map((f) => (
        <div key={f.k} className="tp-fact">
          <span className="tp-fact-v np-num">{f.v}</span>
          <span className="tp-fact-k">{f.k}</span>
        </div>
      ))}
    </div>
  )
}

/** "What comes back" — the promise, itemised.
 *
 *  It earns its place on both pages for the same reason: someone who has never
 *  run one has no idea what "FIFO round trips" or "yield ± 95% CI" buys them,
 *  and the list is more convincing than the phrase. */
export function ToolOutput({ title = 'What comes back', rows }: { title?: string; rows: OutputRow[] }) {
  return (
    <section className="tp-out-wrap">
      <div className="tp-section-head">
        <h2>{title}</h2>
      </div>
      <dl className="tp-out">
        {rows.map((o) => (
          <div key={o.k} className="tp-out-row">
            <dt>{o.k}</dt>
            <dd>{o.v}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
