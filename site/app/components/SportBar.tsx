'use client'

/** Which sport's board you are on.
 *
 *  "Top" is the home page — the biggest games across every sport. The US
 *  sports follow in the order a US bettor's season runs through them, and
 *  soccer has its own page like the rest (/soccer).
 */

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { SPORT_KEYS, SPORT_META } from '../lib/sportsMeta'

const ITEMS = [
  { href: '/', label: 'Top' },
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
