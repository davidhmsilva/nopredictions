'use client'

/** Which sport's board you are on.
 *
 *  The US sports lead, in the order a US bettor's season runs through them.
 *  Soccer has its own page like the rest (/soccer); the home page shows only
 *  the top of that board, so no tab is lit there.
 */

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { SPORT_KEYS, SPORT_META } from '../lib/sportsMeta'

const ITEMS = [
  ...SPORT_KEYS.map((k) => ({ href: SPORT_META[k].path, label: SPORT_META[k].tab ?? SPORT_META[k].label })),
  { href: '/soccer', label: 'Soccer' },
]

export function SportBar() {
  const pathname = usePathname() ?? '/'
  return (
    <nav className="sp-bar" aria-label="Sport">
      <div className="sp-bar-inner">
        {ITEMS.map((i) => {
          const on = pathname === i.href
          return (
            <Link
              key={i.href}
              href={i.href}
              className={`sp-tab${on ? ' is-on' : ''}`}
              aria-current={on ? 'page' : undefined}
            >
              {i.label}
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
