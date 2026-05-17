'use client'

import { useState } from 'react'
import { Nav, MobileNav } from '../components/Nav'
import type { Section } from '../lib/types'
import type { LiveAnalysis } from '../lib/edge'

function pct(v: number) {
  return (v * 100).toFixed(1) + '%'
}

function EdgeBadge({ edge }: { edge: number }) {
  const cls = edge >= 3 ? 'badge-edge' : edge > 0 ? 'badge-slight' : 'badge-neg'
  const sign = edge > 0 ? '+' : ''
  return <span className={`scan-badge ${cls}`}>{sign}{edge.toFixed(1)}pp</span>
}

// ─── Game Analyzer ──────────────────────────────────────────────────────────

function AnalyzeSection() {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<LiveAnalysis | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function analyze() {
    if (!url.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error || `HTTP ${res.status}`)
      }
      setResult(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="scan-section">
      <div className="scan-header">
        <div>
          <h2 className="scan-title">POLYMARKET GAME SCANNER</h2>
          <p className="scan-sub">
            Paste any Polymarket football game URL — live or pre-match. The agent runs our
            Dixon-Coles model and compares against sharp consensus to find any exploitable edge.
          </p>
        </div>
      </div>

      <div className="analyze-input-row">
        <input
          type="text"
          className="analyze-input"
          placeholder="https://polymarket.com/event/elc-sot-mid-2026-05-12"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && analyze()}
        />
        <button className="scan-btn" onClick={analyze} disabled={loading || !url.trim()}>
          {loading ? (
            <span className="scan-btn-loading">
              <span className="scan-spinner" />
              ANALYZING...
            </span>
          ) : (
            'ANALYZE'
          )}
        </button>
      </div>

      {error && <div className="scan-error">Error: {error}</div>}

      {result && <AnalysisResult data={result} />}
    </section>
  )
}

function AnalysisResult({ data }: { data: LiveAnalysis }) {
  const edges = data.pm_markets.filter((m) => m.is_edge)

  return (
    <div className="analysis-result">
      {/* Match header */}
      <div className="analysis-header">
        <div className="analysis-teams">
          <span className="analysis-team">{data.home}</span>
          {data.is_live && data.score ? (
            <span className="analysis-score">
              {data.score}
              <span className="analysis-minute">{data.minute}&apos;</span>
              <span className="analysis-live-badge">LIVE</span>
            </span>
          ) : (
            <span className="analysis-vs">vs</span>
          )}
          <span className="analysis-team">{data.away}</span>
        </div>
        <div className="analysis-meta">
          <span>{data.sport_label}</span>
          <span>
            {new Date(data.commence_time).toLocaleDateString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        </div>
      </div>

      {/* Probabilities comparison */}
      {(data.sharp_odds || data.poisson || data.dc_model) && (
        <div className="analysis-probs">
          <h3 className="scan-group-title">PROBABILITY COMPARISON</h3>
          <table className="scan-table">
            <thead>
              <tr>
                <th>OUTCOME</th>
                {data.dc_model && <th style={{ color: 'var(--accent)' }}>OUR MODEL ★</th>}
                {data.sharp_odds && <th>SHARP CONSENSUS</th>}
                {data.poisson && <th>POISSON IN-PLAY</th>}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{data.home} win</td>
                {data.dc_model && <td style={{ color: 'var(--accent)' }}>{pct(data.dc_model.home_win)}</td>}
                {data.sharp_odds && <td>{pct(data.sharp_odds.home_prob)}</td>}
                {data.poisson && <td>{pct(data.poisson.home_win)}</td>}
              </tr>
              <tr>
                <td>Draw</td>
                {data.dc_model && <td style={{ color: 'var(--accent)' }}>{pct(data.dc_model.draw)}</td>}
                {data.sharp_odds && <td>{data.sharp_odds.draw_prob != null ? pct(data.sharp_odds.draw_prob) : '—'}</td>}
                {data.poisson && <td>{pct(data.poisson.draw)}</td>}
              </tr>
              <tr>
                <td>{data.away} win</td>
                {data.dc_model && <td style={{ color: 'var(--accent)' }}>{pct(data.dc_model.away_win)}</td>}
                {data.sharp_odds && <td>{pct(data.sharp_odds.away_prob)}</td>}
                {data.poisson && <td>{pct(data.poisson.away_win)}</td>}
              </tr>
              <tr>
                <td>Over 2.5</td>
                {data.dc_model && <td style={{ color: 'var(--accent)' }}>{pct(data.dc_model.over_2_5)}</td>}
                {data.sharp_odds && <td>{data.sharp_odds.totals?.find(t => t.line === 2.5) ? pct(data.sharp_odds.totals.find(t => t.line === 2.5)!.over_prob) : '—'}</td>}
                {data.poisson && <td>{pct(data.poisson.over_2_5)}</td>}
              </tr>
              <tr>
                <td>BTTS</td>
                {data.dc_model && <td style={{ color: 'var(--accent)' }}>{pct(data.dc_model.btts)}</td>}
                {data.sharp_odds && <td>—</td>}
                {data.poisson && <td>{data.poisson.btts != null ? pct(data.poisson.btts) : '—'}</td>}
              </tr>
              {data.dc_model && (
                <tr style={{ fontSize: 10, color: 'var(--grey)' }}>
                  <td colSpan={data.sharp_odds && data.poisson ? 4 : data.sharp_odds || data.poisson ? 3 : 2}>
                    λ home: {data.dc_model.lambda_home.toFixed(2)}  ·  λ away: {data.dc_model.lambda_away.toFixed(2)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Edge summary */}
      {edges.length > 0 && (
        <div className="analysis-edges">
          <h3 className="scan-group-title scan-group-edge">
            {edges.length} EDGE{edges.length > 1 ? 'S' : ''} DETECTED
          </h3>
        </div>
      )}

      {/* Market analysis */}
      {data.pm_markets.length > 0 && (
        <div className="analysis-markets">
          <h3 className="scan-group-title">MARKET ANALYSIS</h3>
          {data.pm_markets.map((m, i) => (
            <div key={i} className={`scan-card ${m.is_edge ? 'scan-card-edge' : ''}`}>
              <div className="scan-card-body" style={{ borderTop: 'none' }}>
                <div className="scan-card-row">
                  <div className="scan-card-cell" style={{ flex: 2 }}>
                    <span className="scan-cell-label">MARKET</span>
                    <span className="scan-cell-val" style={{ fontSize: 11 }}>{m.title}</span>
                  </div>
                  <div className="scan-card-cell">
                    <span className="scan-cell-label">PM PRICE</span>
                    <span className="scan-cell-val">{pct(m.pm_price)}</span>
                  </div>
                  {m.dc_prob != null && (
                    <div className="scan-card-cell">
                      <span className="scan-cell-label" style={{ color: 'var(--accent)' }}>OUR MODEL ★</span>
                      <span className="scan-cell-val" style={{ color: 'var(--accent)' }}>{pct(m.dc_prob)}</span>
                      <span className={`scan-cell-odds ${(m.dc_edge_pp ?? 0) > 0 ? 'c-green' : 'c-red'}`}>
                        {m.dc_edge_pp != null ? `${m.dc_edge_pp > 0 ? '+' : ''}${m.dc_edge_pp.toFixed(1)}pp` : ''}
                      </span>
                    </div>
                  )}
                  {m.fair_prob != null && (
                    <div className="scan-card-cell">
                      <span className="scan-cell-label">SHARP</span>
                      <span className="scan-cell-val">{pct(m.fair_prob)}</span>
                      <span className={`scan-cell-odds ${(m.edge_pp ?? 0) > 0 ? 'c-green' : 'c-red'}`}>
                        {m.edge_pp != null ? `${m.edge_pp > 0 ? '+' : ''}${m.edge_pp.toFixed(1)}pp` : ''}
                      </span>
                    </div>
                  )}
                </div>
                <div className="scan-reasoning">{m.reasoning}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {data.pm_markets.length === 0 && (
        <div className="scan-no-edge">
          No active 1X2, O/U, or BTTS markets found for this event on Polymarket.
        </div>
      )}
    </div>
  )
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function ScannerPage() {
  const navigateHome = (s: Section) => {
    window.location.href = s === 'home' ? '/' : `/?section=${s}`
  }

  return (
    <div className="scanner-page">
      <Nav section="home" setSection={navigateHome} />

      <main className="scanner-main">
        <div className="scanner-hero">
          <span className="scanner-eyebrow">EDGE SCANNER</span>
          <h1 className="scanner-h1">Find mispricings.</h1>
          <p className="scanner-hero-sub">
            Paste a Polymarket game link — live or pre-match — and the agent runs our
            Dixon-Coles model to find any exploitable edge.
          </p>
        </div>

        <AnalyzeSection />
      </main>

      <footer className="scanner-footer">
        <span>NOPREDICTIONS</span>
        <span style={{ color: 'var(--grey)' }}>No predictions. Just edges.</span>
      </footer>

      <MobileNav section="home" setSection={navigateHome} />
    </div>
  )
}
