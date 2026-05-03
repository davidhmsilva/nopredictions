import type { Section } from '../lib/types'

export function Footer({ setSection }: { setSection: (s: Section) => void }) {
  return (
    <footer
      style={{
        borderTop: '1px solid var(--border)',
        padding: '24px 32px',
        color: 'var(--grey)',
        fontSize: '11px',
        marginTop: '80px',
      }}
    >
      <div className="footer-inner">
      <div>NOPREDICTIONS © 2026 · AI VS POLYMARKET · NOT FINANCIAL ADVICE</div>
      <div className="footer-links">
        <a
          href="https://x.com"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--grey)', textDecoration: 'none' }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          X / TWITTER
        </a>
        <button
          onClick={() => setSection('newsletter')}
          style={{
            color: 'var(--grey)',
            background: 'none',
            border: 'none',
            fontFamily: 'var(--font)',
            fontSize: '11px',
            cursor: 'pointer',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          NEWSLETTER
        </button>
        <button
          onClick={() => setSection('about')}
          style={{
            color: 'var(--grey)',
            background: 'none',
            border: 'none',
            fontFamily: 'var(--font)',
            fontSize: '11px',
            cursor: 'pointer',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--white)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--grey)')}
        >
          ABOUT
        </button>
      </div>
      </div>
    </footer>
  )
}
