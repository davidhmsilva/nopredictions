/** Inline SVG icons.
 *
 *  Inline rather than an icon font or emoji: emoji render as someone else's
 *  artwork on every platform (and the bell in the nav was already one), and a
 *  font is a network request for nine glyphs. These inherit `currentColor`, so
 *  a chip that turns white turns its icon white too with no extra rule.
 */

type P = { className?: string }

const box = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export function IconAgent({ className }: P) {
  return (
    <svg {...box} className={className}>
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 3v4M9 12.5h.01M15 12.5h.01M9.5 16h5" />
    </svg>
  )
}

/** Create: a spark — an idea becoming something. */
export function IconCreate({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
      <path d="M12 8.5l1.2 2.3 2.3 1.2-2.3 1.2-1.2 2.3-1.2-2.3-2.3-1.2 2.3-1.2z" />
    </svg>
  )
}

/** Marketplace: a shop front with an awning. */
export function IconMarket({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M4 9.5V19a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9.5" />
      <path d="M3 9.5 4.6 4h14.8L21 9.5a2.4 2.4 0 0 1-4.5 1.1 2.6 2.6 0 0 1-4.5 0 2.6 2.6 0 0 1-4.5 0A2.4 2.4 0 0 1 3 9.5Z" />
      <path d="M10 20v-5h4v5" />
    </svg>
  )
}

export function IconAccount({ className }: P) {
  return (
    <svg {...box} className={className}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="10" r="3" />
      <path d="M6.4 18.5a6.5 6.5 0 0 1 11.2 0" />
    </svg>
  )
}

export function IconMenu({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  )
}
