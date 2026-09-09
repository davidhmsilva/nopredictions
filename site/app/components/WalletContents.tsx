'use client'

/** A contents strip for the wallet report.
 *
 *  The report runs to about 4,800 pixels — five screens, eleven sections — and
 *  had no way to move around it except scrolling. This sticks under the header
 *  and marks where you are.
 *
 *  🔑 The section list is READ FROM THE PAGE rather than declared here. The
 *     report renders different sections for different wallets — a wallet with
 *     no in-play trades has no in-play section — so a hard-coded list would
 *     offer links to sections that are not there. Reading the DOM makes the
 *     strip correct by construction for every profile.
 */

import { useEffect, useState } from 'react'

interface Entry {
  id: string
  label: string
}

export function WalletContents() {
  const [items, setItems] = useState<Entry[]>([])
  const [active, setActive] = useState<string | null>(null)

  useEffect(() => {
    const sections = [...document.querySelectorAll<HTMLElement>('.wallet-section[id]')]
    const found = sections
      .map((el) => ({ id: el.id, label: el.querySelector('.wr-h2')?.textContent?.trim() ?? '' }))
      .filter((e) => e.label)
    setItems(found)
    if (found.length === 0) return

    /* ⚠️ Computed from POSITION, not from an IntersectionObserver.
     *
     *  The observer version only fires on a change, and at the top of the page
     *  no section is inside the trigger band — so nothing fired and the strip
     *  kept highlighting whatever had been current last. Scrolling back to the
     *  top left "Where it plays" lit, five screens below where the reader was.
     *
     *  Reading the positions on scroll cannot get into that state: the answer
     *  is derived every time rather than remembered. Eleven elements read once
     *  per animation frame is nothing. */
    /* Where "current" is decided. MEASURED, not a constant: the nav and this
     *  strip are both sticky and both sit above the line, and hard-coding their
     *  combined height is how it silently drifts when either changes. A heading
     *  hidden behind the strip has been passed. */
    const line = () => {
      const nav = document.querySelector('.np-nav')?.getBoundingClientRect().bottom ?? 0
      const strip = document.querySelector('.wr-toc')?.getBoundingClientRect().height ?? 0
      // ⚠️ This must sit BELOW where an anchor jump lands a section, or a
      //    click on the strip highlights the previous entry. `scroll-margin-top`
      //    on `.wallet-section[id]` puts a jumped-to heading at
      //    `--nav-h + 52px`; the +24 here clears that with room to spare. The
      //    two numbers are coupled — change one and check the other.
      return nav + strip + 24
    }
    let frame = 0

    const recompute = () => {
      frame = 0

      // ⚠️ The last section can never reach the line: by the time it would, the
      //    page has run out of scroll. Without this, clicking the final entry
      //    highlights the one before it — which is exactly the sort of "almost
      //    right" that makes a contents strip feel broken.
      const atBottom =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2
      if (atBottom) {
        setActive(sections[sections.length - 1].id)
        return
      }

      const at = line()
      let current: string | null = null
      for (const el of sections) {
        if (el.getBoundingClientRect().top <= at) current = el.id
        else break
      }
      // Above the first heading, nothing is current — which is the honest
      // answer, and better than lighting a section the reader has not reached.
      setActive(current)
    }

    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(recompute)
    }

    recompute()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  if (items.length < 3) return null

  return (
    <nav className="wr-toc" aria-label="Report contents">
      <div className="wr-toc-inner">
        {items.map((it) => (
          <a
            key={it.id}
            href={`#${it.id}`}
            className={`wr-toc-link${active === it.id ? ' is-on' : ''}`}
            aria-current={active === it.id ? 'true' : undefined}
          >
            {it.label}
          </a>
        ))}
      </div>
    </nav>
  )
}
