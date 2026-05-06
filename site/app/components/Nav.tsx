import type { Section } from '../lib/types'

const NAV_LINKS: { id: Section; label: string }[] = [
  { id: 'strategies', label: 'AGENT' },
  { id: 'leaderboard', label: 'LEADERBOARD' },
  { id: 'newsletter', label: 'NEWSLETTER' },
  { id: 'about', label: 'ABOUT' },
]

const MOBILE_NAV: { id: Section; icn: string; label: string }[] = [
  { id: 'home',        icn: '◆', label: 'HOME' },
  { id: 'strategies',  icn: '◇', label: 'AGENT' },
  { id: 'leaderboard', icn: '▲', label: 'RANK' },
  { id: 'about',       icn: '◌', label: 'ABOUT' },
]

export function Nav({
  section,
  setSection,
}: {
  section: Section
  setSection: (s: Section) => void
}) {
  return (
    <nav className="nav-container">
      {/* Logo */}
      <div
        style={{ display: 'flex', flexDirection: 'column', padding: '16px 0', cursor: 'pointer' }}
        onClick={() => setSection('home')}
      >
        <div
          className="nav-logo-title"
          style={{
            fontSize: '20px',
            fontWeight: 'bold',
            letterSpacing: '4px',
            color: 'var(--white)',
          }}
        >
          NOPREDICTIONS
        </div>
        <div
          className="nav-logo-sub"
          style={{ fontSize: '9px', letterSpacing: '3px', color: 'var(--grey)', marginTop: '2px' }}
        >
          AI VS POLYMARKET
        </div>
      </div>

      {/* Links */}
      <div className="nav-links-desktop">
        {NAV_LINKS.map((link) => (
          <button
            key={link.id}
            onClick={() => setSection(link.id)}
            style={{
              padding: '20px 24px',
              color: section === link.id ? 'var(--accent)' : 'var(--grey)',
              cursor: 'pointer',
              fontSize: '12px',
              letterSpacing: '2px',
              background: 'none',
              border: 'none',
              borderBottom: section === link.id ? '2px solid var(--accent)' : '2px solid transparent',
              fontFamily: 'var(--font)',
              transition: 'all 0.15s',
            }}
          >
            {link.label}
          </button>
        ))}
        <a
          href="/scanner"
          style={{
            padding: '20px 24px',
            color: 'var(--grey)',
            cursor: 'pointer',
            fontSize: '12px',
            letterSpacing: '2px',
            textDecoration: 'none',
            fontFamily: 'var(--font)',
            transition: 'all 0.15s',
            borderBottom: '2px solid transparent',
          }}
        >
          SCANNER
        </a>
      </div>

      {/* CTA — desktop only */}
      <button
        className="nav-cta-desktop"
        onClick={() => setSection('newsletter')}
        style={{
          border: '1px solid var(--accent)',
          color: 'var(--accent)',
          padding: '8px 16px',
          fontFamily: 'var(--font)',
          fontSize: '11px',
          letterSpacing: '2px',
          cursor: 'pointer',
          background: 'transparent',
          transition: 'all 0.15s',
        }}
        onMouseOver={(e) => {
          e.currentTarget.style.background = 'var(--accent)'
          e.currentTarget.style.color = 'var(--bg)'
        }}
        onMouseOut={(e) => {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--accent)'
        }}
      >
        FOLLOW THE AGENT ↗
      </button>
    </nav>
  )
}

export function MobileNav({
  section,
  setSection,
}: {
  section: Section
  setSection: (s: Section) => void
}) {
  return (
    <div className="mobile-nav">
      {MOBILE_NAV.map((item) => (
        <button
          key={item.id}
          className={section === item.id ? 'active' : ''}
          onClick={() => setSection(item.id)}
        >
          <span className="icn">{item.icn}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  )
}
