import type { DbStats } from '../lib/supabase'
import { SectionWrap, Spinner } from './ui'

export function AboutSection({ stats, loading }: { stats: DbStats | null; loading: boolean }) {
  if (loading) return <SectionWrap><Spinner /></SectionWrap>

  const earliestYear = stats?.earliestMatch
    ? new Date(stats.earliestMatch).getFullYear()
    : 2010
  const latestYear = stats?.latestMatch
    ? new Date(stats.latestMatch).getFullYear()
    : new Date().getFullYear()

  return (
    <SectionWrap>
      <div style={{ maxWidth: '720px' }}>
        {/* Intro */}
        <div
          className="rg-1-2"
          style={{
            gap: '48px',
            marginBottom: '48px',
            paddingBottom: '48px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="about-avatar">◈</div>
          <div>
            <div style={{ fontSize: '22px', letterSpacing: '3px', marginBottom: '8px' }}>
              DAVID SILVA
            </div>
            <div
              style={{
                color: 'var(--grey)',
                fontSize: '12px',
                letterSpacing: '2px',
                marginBottom: '24px',
              }}
            >
              16 YEARS IN MARKETS · NOW WITH AN AI CO-PILOT
            </div>
            <div style={{ fontSize: '13px', color: '#aaa', lineHeight: '1.9' }}>
              <p style={{ marginBottom: '16px' }}>
                I&apos;ve been involved in sports betting and prediction markets for 16 years. Not as
                a hobby — seriously. I&apos;ve studied closing lines, tracked edges, managed
                bankrolls through long losing runs, and felt the discipline required to not blow
                everything when you&apos;re wrong.
              </p>
              <p style={{ marginBottom: '16px' }}>
                I&apos;ve had wins. I&apos;ve had periods where everything I touched turned to
                nothing. I know what a real edge looks like — and I know how hard it is to find
                one consistently.
              </p>
              <p>
                With AI getting genuinely capable, I had one question: can an AI agent do what I
                couldn&apos;t do systematically? Can it find edges in prediction markets that
                human intuition misses? This project is the answer to that question — run live,
                in public, with real stakes.
              </p>
            </div>
          </div>
        </div>

        {/* Real stats */}
        <div
          className="rg-4"
          style={{
            gap: '16px',
            marginBottom: '48px',
          }}
        >
          {[
            { num: '16', label: 'YEARS IN MARKETS' },
            {
              num: stats ? stats.matches.toLocaleString() : '—',
              label: 'MATCHES IN DB',
            },
            {
              num: stats ? stats.oddsRecords.toLocaleString() : '—',
              label: 'ODDS RECORDS',
            },
            {
              num: `${earliestYear}→${latestYear}`,
              label: 'DATA RANGE',
            },
          ].map((s) => (
            <div
              key={s.label}
              style={{
                background: 'var(--bg2)',
                border: '1px solid var(--border)',
                padding: '20px',
                textAlign: 'center',
              }}
            >
              <div
                style={{
                  fontSize: s.num.length > 6 ? '18px' : '28px',
                  color: 'var(--accent)',
                  marginBottom: '4px',
                  fontWeight: 'bold',
                }}
              >
                {s.num}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--grey)', letterSpacing: '2px' }}>
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* Leagues covered */}
        <div
          style={{
            border: '1px solid var(--border)',
            padding: '24px',
            marginBottom: '32px',
          }}
        >
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '20px',
            }}
          >
            LEAGUES IN DATABASE ({stats?.leagues ?? 27})
          </div>
          <div
            className="rg-3"
            style={{
              gap: '8px',
              fontSize: '11px',
              color: 'var(--grey)',
            }}
          >
            {[
              'Premier League', 'Championship', 'League One', 'League Two',
              'La Liga', 'Segunda División', 'Serie A', 'Serie B',
              'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2',
              'Eredivisie', 'Pro League (Belgium)', 'Liga Portugal', 'Süper Lig',
              'Super League (Greece)', 'Scottish Premiership', 'Scottish Championship',
              'Scottish League One', 'Scottish League Two',
              'Champions League', 'Europa League',
              'World Cup', 'European Championship', 'Copa América', 'Nations League',
            ].map((l) => (
              <div key={l} style={{ padding: '4px 0', borderBottom: '1px solid #161616' }}>
                <span style={{ color: 'var(--accent)' }}>·</span> {l}
              </div>
            ))}
          </div>
        </div>

        {/* Rules */}
        <div style={{ border: '1px solid var(--border)', padding: '24px' }}>
          <div
            style={{
              fontSize: '12px',
              letterSpacing: '3px',
              color: 'var(--grey)',
              marginBottom: '20px',
            }}
          >
            THE RULES THE AGENT FOLLOWS
          </div>
          {[
            {
              n: '1',
              title: 'No lookahead bias.',
              text: 'Every data point used must have been available before kickoff. The agent can only use information a bettor would have had at the time.',
            },
            {
              n: '2',
              title: 'CLV is king.',
              text: 'Positive ROI with negative Closing Line Value = luck. Positive CLV with negative ROI (short-term) = potential edge. We always measure both.',
            },
            {
              n: '3',
              title: 'Minimum 200 selections',
              text: 'before any strategy conclusion. No cherry-picking a 15-bet hot streak.',
            },
            {
              n: '4',
              title: 'Every failure gets logged.',
              text: 'Every position is logged — wins and losses. No quiet deletions.',
            },
            {
              n: '5',
              title: 'All picks posted before kickoff.',
              text: "No retroactive claims. The agent puts its reasoning on the table before the match starts.",
            },
          ].map((rule) => (
            <div
              key={rule.n}
              style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}
            >
              <div
                style={{
                  color: 'var(--accent)',
                  fontSize: '18px',
                  flexShrink: 0,
                  width: '24px',
                }}
              >
                {rule.n}
              </div>
              <div style={{ fontSize: '12px', color: '#aaa', lineHeight: '1.7' }}>
                <strong style={{ color: 'var(--white)' }}>{rule.title}</strong> {rule.text}
              </div>
            </div>
          ))}
        </div>
      </div>
    </SectionWrap>
  )
}
