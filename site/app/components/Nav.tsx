import type { Section } from '../lib/types'

const NAV_LINKS: { id: Section; label: string }[] = [
  { id: 'agent', label: 'AGENT' },
  { id: 'newsletter', label: 'NEWSLETTER' },
  { id: 'about', label: 'ABOUT' },
]

const MOBILE_NAV: { id: Section; icn: string; label: string }[] = [
  { id: 'home',   icn: '◆', label: 'HOME' },
  { id: 'agent',  icn: '◇', label: 'AGENT' },
  { id: 'about',  icn: '◌', label: 'ABOUT' },
]

const SCANNER_LINK = { icn: '⊕', label: 'SCAN' }

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
        className="nav-brand"
        onClick={() => setSection('home')}
      >
        <div
          className="nav-logo-title"
          style={{ fontSize: '16px', fontWeight: 'bold', letterSpacing: '4px', color: 'var(--white)' }}
        >
          NOPREDICTIONS
        </div>
        <div
          className="nav-logo-sub"
          style={{ fontSize: '9px', letterSpacing: '3px', color: 'var(--green)', marginTop: '1px' }}
        >
          AI VS PREDICTION MARKETS
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
  const isScanner = typeof window !== 'undefined' && window.location.pathname === '/scanner'

  return (
    <div className="mobile-nav">
      <button
        className={section === 'home' && !isScanner ? 'active' : ''}
        onClick={() => { window.location.href = '/dashboard'; }}
      >
        <span className="icn">◆</span>
        <span>HOME</span>
      </button>
      <a href="/scanner" className={isScanner ? 'active' : ''}>
        <span className="icn">{SCANNER_LINK.icn}</span>
        <span>{SCANNER_LINK.label}</span>
      </a>
      {MOBILE_NAV.filter((item) => item.id !== 'home').map((item) => (
        <button
          key={item.id}
          className={section === item.id && !isScanner ? 'active' : ''}
          onClick={() => setSection(item.id)}
        >
          <span className="icn">{item.icn}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  )
}
