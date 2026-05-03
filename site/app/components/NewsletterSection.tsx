'use client'

import { useState } from 'react'
import { SectionWrap } from './ui'

export function NewsletterSection() {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // TODO: wire to email provider (Resend, Mailchimp, etc.)
    setSubmitted(true)
  }

  return (
    <SectionWrap>
      <div style={{ maxWidth: '560px', margin: '60px auto', textAlign: 'center' }}>
        <h1 style={{ fontSize: '28px', letterSpacing: '4px', marginBottom: '16px' }}>
          FOLLOW THE AGENT
        </h1>
        <p style={{ color: 'var(--grey)', fontSize: '12px', lineHeight: '1.8', marginBottom: '40px' }}>
          Get weekly updates: every position the agent opens, edge reports,
          and P&amp;L results. No noise. Just the data.
        </p>

        {submitted ? (
          <div
            style={{
              background: 'var(--bg2)',
              border: '1px solid var(--green)',
              padding: '24px',
              color: 'var(--green)',
              letterSpacing: '2px',
              fontSize: '13px',
            }}
          >
            ✓ YOU&apos;RE IN. WE&apos;LL BE IN TOUCH.
          </div>
        ) : (
          <>
            <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 0, marginBottom: '16px' }}>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                style={{
                  flex: 1,
                  background: 'var(--bg2)',
                  border: '1px solid var(--border)',
                  borderRight: 'none',
                  color: 'var(--white)',
                  fontFamily: 'var(--font)',
                  fontSize: '13px',
                  padding: '14px 16px',
                  outline: 'none',
                }}
              />
              <button
                type="submit"
                style={{
                  background: 'var(--accent)',
                  color: 'var(--bg)',
                  border: 'none',
                  padding: '14px 24px',
                  fontFamily: 'var(--font)',
                  fontSize: '12px',
                  letterSpacing: '2px',
                  cursor: 'pointer',
                  fontWeight: 'bold',
                }}
              >
                SUBSCRIBE
              </button>
            </form>
            <div style={{ fontSize: '11px', color: 'var(--grey)' }}>
              No spam. Unsubscribe anytime. Every pick posted before kickoff.
            </div>
          </>
        )}

        {/* What you get */}
        <div
          style={{
            textAlign: 'left',
            marginTop: '60px',
            borderTop: '1px solid var(--border)',
            paddingTop: '40px',
          }}
        >
          <h3
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '24px',
            }}
          >
            WHAT YOU GET EVERY WEEK
          </h3>
          {[
            {
              title: 'All live positions',
              text: 'Every position the agent opens, with full reasoning, before the match starts. No hindsight.',
            },
            {
              title: 'Edge reports',
              text: 'What mispricings the agent found this week — which markets diverged from sharp consensus and by how much.',
            },
            {
              title: 'Weekly P&L',
              text: 'Honest accounting. Units won, units lost, CLV captured. Good weeks and bad weeks alike.',
            },
            {
              title: 'Research graveyard',
              text: "Strategies that failed and exactly why. The failures are as important as the wins.",
            },
          ].map((item) => (
            <div key={item.title} style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
              <div style={{ color: 'var(--accent)', fontSize: '16px', flexShrink: 0 }}>→</div>
              <div style={{ fontSize: '12px', color: 'var(--grey)', lineHeight: '1.6' }}>
                <strong style={{ color: 'var(--white)' }}>{item.title}</strong> — {item.text}
              </div>
            </div>
          ))}
        </div>
      </div>
    </SectionWrap>
  )
}
