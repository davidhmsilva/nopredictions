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

export function IconBoard({ className }: P) {
  return (
    <svg {...box} className={className}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M9 9v11" />
    </svg>
  )
}

export function IconLab({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M9 3v6.5L4.2 17.4A2 2 0 0 0 5.9 20.5h12.2a2 2 0 0 0 1.7-3.1L15 9.5V3" />
      <path d="M8 3h8M7.4 14h9.2" />
    </svg>
  )
}

export function IconAgent({ className }: P) {
  return (
    <svg {...box} className={className}>
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 3v4M9 12.5h.01M15 12.5h.01M9.5 16h5" />
    </svg>
  )
}

export function IconWallet({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a2 2 0 0 1 2 2v1" />
      <rect x="3" y="7.5" width="18" height="12" rx="2.5" />
      <path d="M16.5 13.5h.01" />
    </svg>
  )
}

export function IconSearch({ className }: P) {
  return (
    <svg {...box} className={className}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
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

export function IconBell({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M18 8.5a6 6 0 1 0-12 0c0 5-2 6.5-2 6.5h16s-2-1.5-2-6.5" />
      <path d="M10.4 19a2 2 0 0 0 3.2 0" />
    </svg>
  )
}

// ── category-bar glyphs ──────────────────────────────────────────────────────

export function IconAll({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  )
}

export function IconLive({ className }: P) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden className={className}>
      <circle cx="12" cy="12" r="5" />
    </svg>
  )
}

export function IconClock({ className }: P) {
  return (
    <svg {...box} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 1.8" />
    </svg>
  )
}

/** Two exchanges, side by side. Marks the filter for the games both list —
 *  the only ones where a cheaper venue exists to find. */
export function IconVenues({ className }: P) {
  return (
    <svg {...box} className={className}>
      <rect x="3" y="7" width="7.5" height="10" rx="1.5" />
      <rect x="13.5" y="7" width="7.5" height="10" rx="1.5" />
      <path d="M10.5 12h3" />
    </svg>
  )
}

export function IconBook({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M12 5.5 5 3.5v15l7 2 7-2v-15z" />
      <path d="M12 5.5v15" />
    </svg>
  )
}

export function IconRuler({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M4 15.5 15.5 4l4.5 4.5L8.5 20z" />
      <path d="M8 11.5 9.8 13.3M11 8.5l1.8 1.8M14 5.5l1.8 1.8" />
    </svg>
  )
}

/** Dropping odds — a line falling, with the arrowhead down. The one thing the
 *  icon must not suggest is a recommendation, so it is a chart movement and
 *  not a thumbs-up, a flame or a rocket. */
export function IconDrop({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M4 7l5 5 3-3 5 5" />
      <path d="M17 10v4h-4" />
      <path d="M4 19h16" />
    </svg>
  )
}

/** Insights — a page with a line of text and a rule under it. Deliberately not
 *  a newspaper or a lightbulb: this section is measurements, not opinion. */
export function IconInsights({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4" />
      <path d="M9 12h6M9 16h4" />
    </svg>
  )
}

export function IconStar({ className }: P) {
  return (
    <svg {...box} className={className}>
      <path d="m12 4 2.5 5.2 5.5.8-4 3.9 1 5.6L12 16.9 7 19.5l1-5.6-4-3.9 5.5-.8z" />
    </svg>
  )
}
