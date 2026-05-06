'use client'

import { useState } from 'react'
import type { ScanResult, AnalyzedMarket, LiveAnalysis } from '../lib/edge'

function pct(v: number) {
  return (v * 100).toFixed(1) + '%'
}

function EdgeBadge({ edge }: { edge: number }) {
  const cls = edge >= 3 ? 'badge-edge' : edge > 0 ? 'badge-slight' : 'badge-neg'
  const sign = edge > 0 ? '+' : ''
  return <span className={`scan-badge ${cls}`}>{sign}{edge.toFixed(1)}pp</span>
}

function oddsFromProb(p: number) {
  return p > 0 ? (1 / p).toFixed(2) : '—'
}

// ─── Pre-match Scanner ──────────────────────────────────────────────────────

function ScanSection() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function runScan() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/scan')
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

  const edges = result?.analyzed.filter((a) => a.is_edge) ?? []
  const noEdge = result?.analyzed.filter((a) => !a.is_edge) ?? []

  // Group no-edge by event for compact display
  const noEdgeByEvent: Record<string, AnalyzedMarket[]> = {}
  for (const m of noEdge) {
    const key = `${m.home}_${m.away}`
    if (!noEdgeByEvent[key]) noEdgeByEvent[key] = []
    noEdgeByEvent[key].push(m)
  }

  return (
    <section className="scan-section">
      <div className="scan-header">
        <div>
          <h2 className="scan-title">PRE-MATCH SCANNER</h2>
          <p className="scan-sub">
            Scans all football markets on Polymarket for the next 3 days.
            Compares prices against Pinnacle + Betfair Exchange sharp consensus.
          </p>
        </div>
        <button className="scan-btn" onClick={runScan} disabled={loading}>
          {loading ? (
            <span className="scan-btn-loading">
              <span className="scan-spinner" />
              SCANNING...
            </span>
          ) : (
            'SCAN NEXT 3 DAYS'
          )}
        </button>
      </div>

      {error && <div className="scan-error">Error: {error}</div>}

      {result && (
        <div className="scan-results">
          {/* Summary bar */}
          <div className="scan-summary">
            <div className="scan-stat">
              <span className="scan-stat-val">{result.events_analyzed}</span>
              <span className="scan-stat-lbl">MATCHES</span>
            </div>
            <div className="scan-stat">
              <span className="scan-stat-val">{result.markets_analyzed}</span>
              <span className="scan-stat-lbl">MARKETS</span>
            </div>
            <div className="scan-stat">
              <span className="scan-stat-val scan-stat-edge">{result.edges_found}</span>
              <span className="scan-stat-lbl">EDGES FOUND</span>
            </div>
            {result.odds_api_remaining && (
              <div className="scan-stat">
                <span className="scan-stat-val">{result.odds_api_remaining}</span>
                <span className="scan-stat-lbl">API CALLS LEFT</span>
              </div>
            )}
          </div>

          {/* Edges */}
          {edges.length > 0 && (
            <div className="scan-group">
              <h3 className="scan-group-title scan-group-edge">EDGES DETECTED</h3>
              {edges.map((m, i) => (
                <MarketCard key={i} market={m} expanded />
              ))}
            </div>
          )}

          {edges.length === 0 && result.markets_analyzed > 0 && (
            <div className="scan-no-edge">
              No edges found above the 3pp threshold. The markets below were analyzed
              — here's why none qualified.
            </div>
          )}

          {/* Analyzed (no edge) */}
          {Object.keys(noEdgeByEvent).length > 0 && (
            <div className="scan-group">
              <h3 className="scan-group-title">ANALYZED MATCHES</h3>
              {Object.entries(noEdgeByEvent).map(([key, markets]) => (
                <EventGroup key={key} markets={markets} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function MarketCard({ market: m, expanded }: { market: AnalyzedMarket; expanded?: boolean }) {
  const [open, setOpen] = useState(expanded ?? false)

  return (
    <div className={`scan-card ${m.is_edge ? 'scan-card-edge' : ''}`}>
      <div className="scan-card-head" onClick={() => setOpen(!open)}>
        <div className="scan-card-info">
          <span className="scan-card-sport">{m.sport_label}</span>
          <span className="scan-card-match">{m.home} vs {m.away}</span>
          <span className="scan-card-time">
            {new Date(m.commence_time).toLocaleDateString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        </div>
        <div className="scan-card-edge-info">
          <span className="scan-card-outcome">{m.outcome_label}</span>
          <EdgeBadge edge={m.edge_pp} />
        </div>
      </div>
      {open && (
        <div className="scan-card-body">
          <div className="scan-card-row">
            <div className="scan-card-cell">
              <span className="scan-cell-label">PM PRICE</span>
              <span className="scan-cell-val">{pct(m.pm_price)}</span>
              <span className="scan-cell-odds">({oddsFromProb(m.pm_price)})</span>
            </div>
            <div className="scan-card-cell">
              <span className="scan-cell-label">SHARP FAIR</span>
              <span className="scan-cell-val">{pct(m.sharp_prob)}</span>
              <span className="scan-cell-odds">({oddsFromProb(m.sharp_prob)})</span>
            </div>
            <div className="scan-card-cell">
              <span className="scan-cell-label">EDGE</span>
              <span className={`scan-cell-val ${m.edge_pp > 0 ? 'c-green' : 'c-red'}`}>
                {m.edge_pp > 0 ? '+' : ''}{m.edge_pp.toFixed(1)}pp
              </span>
            </div>
            <div className="scan-card-cell">
              <span className="scan-cell-label">EV</span>
              <span className={`scan-cell-val ${m.ev_pct > 0 ? 'c-green' : 'c-red'}`}>
                {m.ev_pct > 0 ? '+' : ''}{m.ev_pct.toFixed(1)}%
              </span>
            </div>
          </div>
          <div className="scan-reasoning">{m.reasoning}</div>
        </div>
      )}
    </div>
  )
}

function EventGroup({ markets }: { markets: AnalyzedMarket[] }) {
  const [open, setOpen] = useState(false)
  const first = markets[0]
  const bestEdge = Math.max(...markets.map((m) => m.edge_pp))

  return (
    <div className="scan-event-group">
      <div className="scan-event-head" onClick={() => setOpen(!open)}>
        <div className="scan-event-info">
          <span className="scan-card-sport">{first.sport_label}</span>
          <span className="scan-card-match">{first.home} vs {first.away}</span>
          <span className="scan-card-time">
            {new Date(first.commence_time).toLocaleDateString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        </div>
        <div className="scan-event-meta">
          <span className="scan-event-count">{markets.length} market{markets.length > 1 ? 's' : ''}</span>
          <EdgeBadge edge={bestEdge} />
          <span className="scan-expand">{open ? '▲' : '▼'}</span>
        </div>
      </div>
      {open && (
        <div className="scan-event-body">
          <table className="scan-table">
            <thead>
              <tr>
                <th>OUTCOME</th>
                <th>PM PRICE</th>
                <th>SHARP FAIR</th>
                <th>EDGE</th>
              </tr>
            </thead>
            <tbody>
              {markets.map((m, i) => (
                <tr key={i}>
                  <td>{m.outcome_label}</td>
                  <td>{pct(m.pm_price)}</td>
                  <td>{pct(m.sharp_prob)}</td>
                  <td>
                    <span className={m.edge_pp > 0 ? 'c-green' : 'c-red'}>
                      {m.edge_pp > 0 ? '+' : ''}{m.edge_pp.toFixed(1)}pp
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="scan-reasoning">{markets[0].reasoning}</div>
        </div>
      )}
    </div>
  )
}

// ─── Live Game Analyzer ─────────────────────────────────────────────────────

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
          <h2 className="scan-title">LIVE GAME ANALYZER</h2>
          <p className="scan-sub">
            Paste a Polymarket football game URL. The agent will fetch live score,
            run the Poisson model, compare against sharp odds, and show you where the value is.
          </p>
        </div>
      </div>

      <div className="analyze-input-row">
        <input
          type="text"
          className="analyze-input"
          placeholder="https://polymarket.com/event/ucl-bay-psg-2026-05-06"
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
      {(data.sharp_odds || data.poisson) && (
        <div className="analysis-probs">
          <h3 className="scan-group-title">PROBABILITY COMPARISON</h3>
          <table className="scan-table">
            <thead>
              <tr>
                <th>OUTCOME</th>
                {data.sharp_odds && <th>SHARP CONSENSUS</th>}
                {data.poisson && <th>POISSON MODEL</th>}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{data.home} win</td>
                {data.sharp_odds && <td>{pct(data.sharp_odds.home_prob)}</td>}
                {data.poisson && <td>{pct(data.poisson.home_win)}</td>}
              </tr>
              <tr>
                <td>Draw</td>
                {data.sharp_odds && <td>{data.sharp_odds.draw_prob != null ? pct(data.sharp_odds.draw_prob) : '—'}</td>}
                {data.poisson && <td>{pct(data.poisson.draw)}</td>}
              </tr>
              <tr>
                <td>{data.away} win</td>
                {data.sharp_odds && <td>{pct(data.sharp_odds.away_prob)}</td>}
                {data.poisson && <td>{pct(data.poisson.away_win)}</td>}
              </tr>
              {data.sharp_odds?.totals?.map((t) => (
                <tr key={t.line}>
                  <td>Over/Under {t.line}</td>
                  <td>{pct(t.over_prob)} / {pct(t.under_prob)}</td>
                  {data.poisson && (
                    <td>
                      {t.line === 2.5
                        ? `${pct(data.poisson.over_2_5)} / ${pct(data.poisson.under_2_5)}`
                        : t.line === 1.5
                          ? `${pct(data.poisson.over_1_5)} / ${pct(data.poisson.under_1_5)}`
                          : '—'}
                    </td>
                  )}
                </tr>
              ))}
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
                  <div className="scan-card-cell">
                    <span className="scan-cell-label">FAIR VALUE</span>
                    <span className="scan-cell-val">
                      {m.fair_prob != null ? pct(m.fair_prob) : '—'}
                    </span>
                  </div>
                  <div className="scan-card-cell">
                    <span className="scan-cell-label">EDGE</span>
                    <span className={`scan-cell-val ${(m.edge_pp ?? 0) > 0 ? 'c-green' : (m.edge_pp ?? 0) < 0 ? 'c-red' : ''}`}>
                      {m.edge_pp != null ? `${m.edge_pp > 0 ? '+' : ''}${m.edge_pp.toFixed(1)}pp` : '—'}
                    </span>
                  </div>
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
  return (
    <div className="scanner-page">
      <nav className="scanner-nav">
        <a href="/" className="scanner-logo">
          <span className="scanner-logo-title">NOPREDICTIONS</span>
          <span className="scanner-logo-sub">AI VS POLYMARKET</span>
        </a>
        <a href="/" className="scanner-back">← DASHBOARD</a>
      </nav>

      <main className="scanner-main">
        <div className="scanner-hero">
          <span className="scanner-eyebrow">EDGE SCANNER</span>
          <h1 className="scanner-h1">Find mispricings.</h1>
          <p className="scanner-hero-sub">
            Scan Polymarket football markets against Pinnacle + Betfair sharp consensus,
            or analyze a specific live game with the Poisson in-play model.
          </p>
        </div>

        <ScanSection />
        <div className="scanner-divider" />
        <AnalyzeSection />
      </main>

      <footer className="scanner-footer">
        <span>NOPREDICTIONS</span>
        <span style={{ color: 'var(--grey)' }}>No predictions. Just edges.</span>
      </footer>
    </div>
  )
}
