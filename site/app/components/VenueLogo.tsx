/** The two exchanges' own marks, drawn small.
 *
 *  They replace the boxed "P" and "K" tags. A letter had to be learned; a logo
 *  is already known to anyone who has opened either app, and it is the one
 *  place on the board that is allowed a colour — the colour IS the brand.
 *
 *  Paths are the venues' own: Polymarket's pinned-tab mark (polymarket.com
 *  /icons/safari-pinned-tab.svg, drawn white on its #1652F0 square) and the
 *  "K" of Kalshi's wordmark on its green square, as its app icon draws it.
 *  Inline rather than an <img>: no request, crisp at any size, and it renders
 *  on the first paint with the price beside it.
 */

import type { Venue } from '../lib/venues'
import { VENUE_NAME } from '../lib/venues'

const PM_MARK =
  'M10445 15709 c-2667 -764 -4860 -1391 -4872 -1394 l-23 -5 0 -3345 0 -3344 23 -7 ' +
  'c79 -25 9722 -2782 9724 -2780 2 1 2 2761 1 6133 l-3 6129 -4850 -1387z ' +
  'm3915 -1910 c0 -1939 -1 -2041 -17 -2037 -160 43 -7100 2032 -7105 2037 -7 6 7068 2037 7105 2040 ' +
  '16 1 17 -102 17 -2040z m-4263 -1806 c1976 -565 3591 -1028 3590 -1029 -4 -4 -7141 -2045 -7169 -2051 ' +
  'l-28 -5 0 2056 c0 1131 3 2056 8 2056 4 0 1623 -462 3599 -1027z m4263 -3864 l0 -2041 -27 5 ' +
  'c-16 3 -1617 460 -3560 1016 -1942 556 -3535 1011 -3539 1011 -4 0 -4 3 0 7 5 6 7095 2040 7119 2042 ' +
  '4 1 7 -918 7 -2040z'

const KALSHI_K =
  'M105.23 105.628L179.66 222.61H115.118L54.3009 121.934V222.61H0V3.38607H54.3009V99.102' +
  'L119.464 3.38607H177.489L105.23 105.628Z'

export function VenueLogo({
  venue,
  size = 14,
  className = '',
  title,
}: {
  venue: Venue
  size?: number
  className?: string
  /** Hover text. Defaults to the venue's name; pass '' to leave it to a parent. */
  title?: string
}) {
  const label = title ?? VENUE_NAME[venue]
  const common = {
    width: size,
    height: size,
    className: `np-venue-logo ${className}`,
    role: 'img' as const,
    'aria-label': VENUE_NAME[venue],
  }
  if (venue === 'polymarket') {
    return (
      <svg {...common} viewBox="20 65 2044 2044">
        {label && <title>{label}</title>}
        <rect x="20" y="65" width="2044" height="2044" rx="460" fill="#1652F0" />
        <g transform="translate(0,2184) scale(0.1,-0.1)" fill="#fff">
          <path d={PM_MARK} />
        </g>
      </svg>
    )
  }
  return (
    <svg {...common} viewBox="-110 -87 400 400">
      {label && <title>{label}</title>}
      <rect x="-110" y="-87" width="400" height="400" rx="90" fill="#28CC95" />
      <path d={KALSHI_K} fill="#0b0d10" />
    </svg>
  )
}
