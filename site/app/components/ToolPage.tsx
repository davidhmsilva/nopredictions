'use client'

/** The shared frame for a tool page: the eyebrow, the headline, the chips.
 *  Made once, so the next tool page gets the same scale and colour for free. */

import type { ReactNode } from 'react'

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

/** Shortcuts under the box: whole sentences to try. */
export function ToolChips({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="tp-chips">
      {label && <div className="tp-chips-label">{label}</div>}
      <div className="tp-chips-row">{children}</div>
    </div>
  )
}
