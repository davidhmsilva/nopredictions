import { SectionWrap, SectionTitle, Spinner } from './ui'
import type { DbStats } from '../lib/supabase'

export function AboutSection({ stats, loading }: { stats: DbStats | null; loading: boolean }) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  return (
    <SectionWrap>
      <SectionTitle title="ABOUT" sub="THE EXPERIMENT" />

      <div style={{ maxWidth: '560px', margin: '0 auto', textAlign: 'center' }}>

        <p style={{ fontSize: '14px', color: '#aaa', lineHeight: '2', marginBottom: '32px' }}>
          This is a live experiment. An AI agent scans sports prediction markets every day —
          football and NBA — looking for prices the market got wrong. When it finds one,
          it bets — and logs everything here in real time.
        </p>

        <p style={{ fontSize: '14px', color: '#aaa', lineHeight: '2', marginBottom: '32px' }}>
          No predictions. No tips. Just a model with a thesis, running in public,
          winning and losing in real time.
        </p>

        <p style={{ fontSize: '14px', color: '#aaa', lineHeight: '2', marginBottom: '48px' }}>
          The question isn&apos;t whether AI can beat a prediction market. The question
          is whether you can watch it try — and learn something about markets, about
          models, and about edges that quietly disappear the moment everyone finds them.
        </p>

        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '1px',
          background: 'var(--border)',
          border: '1px solid var(--border)',
          marginBottom: '48px',
        }}>
          {[
            { v: stats ? stats.matches.toLocaleString() : '—', l: 'MATCHES TRAINED ON' },
            { v: '100%', l: 'POSITIONS PUBLIC' },
          ].map(s => (
            <div key={s.l} style={{ background: 'var(--bg2)', padding: '24px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: '20px', color: 'var(--accent)', fontWeight: 'bold', marginBottom: '6px' }}>{s.v}</div>
              <div style={{ fontSize: '9px', color: 'var(--grey)', letterSpacing: '2px' }}>{s.l}</div>
            </div>
          ))}
        </div>

        <div style={{
          borderTop: '1px solid var(--border)',
          paddingTop: '32px',
          fontSize: '11px',
          color: 'var(--grey)',
          lineHeight: '2',
          letterSpacing: '1px',
        }}>
          Every pick is logged publicly. Every loss is kept on record.
          No retroactive claims. No quiet deletions.
        </div>

      </div>
    </SectionWrap>
  )
}
