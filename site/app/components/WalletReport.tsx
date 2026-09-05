'use client'

import type { WalletProfile } from '../lib/wallet'

// ─── formatting ─────────────────────────────────────────────────────────────

function money(x: number | null | undefined): string {
  if (x === null || x === undefined) return '—'
  const sign = x < 0 ? '-' : ''
  const a = Math.abs(x)
  return a >= 1000
    ? `${sign}$${a.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    : `${sign}$${a.toFixed(2)}`
}

function signed(x: number | null | undefined, dp = 1): string {
  if (x === null || x === undefined) return '—'
  return `${x >= 0 ? '+' : ''}${x.toFixed(dp)}%`
}

function dur(min: number | null | undefined): string {
  if (min === null || min === undefined) return '—'
  if (min < 60) return `${min.toFixed(1)} min`
  if (min < 1440) return `${(min / 60).toFixed(1)} h`
  return `${(min / 1440).toFixed(1)} d`
}

const day = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)

/** The narrative is written with **bold**, *italic* and `code`. Rendering it as
 *  plain text loses the emphasis that carries the reading, and dangling
 *  asterisks look like a bug. This is the whole of the markdown we generate. */
function Rich({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g)
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
        if (p.startsWith('`') && p.endsWith('`')) return <code key={i} className="wallet-code">{p.slice(1, -1)}</code>
        if (p.startsWith('*') && p.endsWith('*') && p.length > 2) return <em key={i}>{p.slice(1, -1)}</em>
        return <span key={i}>{p}</span>
      })}
    </>
  )
}

function Stat({ label, value, tone, sub }: { label: string; value: string; tone?: 'green' | 'red'; sub?: string }) {
  return (
    <div className="wallet-stat">
      <div className="wallet-stat-label">{label}</div>
      <div className={`wallet-stat-value${tone ? ` c-${tone}` : ''}`}>{value}</div>
      {sub ? <div className="wallet-stat-sub">{sub}</div> : null}
    </div>
  )
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="wallet-section">
      <h2 className="wallet-h2">
        {title}
        {sub ? <span className="wallet-h2-sub">{sub}</span> : null}
      </h2>
      {children}
    </section>
  )
}

/** A bar that reads left-to-right from zero, so a negative row is visibly a
 *  different thing from a small positive one rather than just shorter. */
function Bar({ value, max }: { value: number; max: number }) {
  const w = max ? Math.min(100, (Math.abs(value) / max) * 100) : 0
  return (
    <span className="wallet-bar">
      <span className={`wallet-bar-fill ${value >= 0 ? 'pos' : 'neg'}`} style={{ width: `${w}%` }} />
    </span>
  )
}

// ─── report ─────────────────────────────────────────────────────────────────

export function WalletReport({ p }: { p: WalletProfile }) {
  const t = p.totals
  const cov = p.coverage
  const boot = p.bootstrap
  const maxMonthPnl = Math.max(...p.months.map((m) => Math.abs(m.pnl)), 1)
  const maxBandCost = Math.max(...p.entry_bands.map((b) => b.cost), 1)

  const timingRows: [string, string][] = [
    ['prematch', 'pre-match (before kick-off)'],
    ['in_match', 'in-match (0–110′)'],
    ['whistle', 'whistle (110–130′)'],
    ['settle', 'settle (130′+)'],
    ['unknown', 'no kick-off time'],
  ]

  return (
    <div className="wallet-report">
      {/* ── identity ── */}
      <header className="wallet-head">
        {p.profile_image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={p.profile_image} alt="" className="wallet-avatar" />
        ) : (
          <div className="wallet-avatar wallet-avatar-blank">?</div>
        )}
        <div>
          <h1 className="wallet-name">{p.name || p.pseudonym || 'Unnamed wallet'}</h1>
          <a
            className="wallet-addr"
            href={`https://polymarket.com/profile/${p.wallet}`}
            target="_blank"
            rel="noreferrer"
          >
            {p.wallet}
          </a>
          <div className="wallet-meta">
            {cov.rows.toLocaleString('en-US')} activity rows · {day(cov.first_ts)} → {day(cov.last_ts)} ·{' '}
            {cov.days_active} active days
            {!cov.complete && <span className="wallet-warn"> · PARTIAL HISTORY</span>}
          </div>
        </div>
      </header>

      {/* A report you cannot trust must say so before it says anything else —
          the caveat at the bottom of a long page is a caveat nobody reads. */}
      {p.trust !== 'ok' && (
        <div className="wallet-banner">
          <strong>
            {p.trust === 'partial' ? 'PARTIAL HISTORY' : 'DOES NOT RECONCILE'}
          </strong>
          <span>
            {p.trust === 'partial'
              ? `The activity walk stopped before the start of this account — ${p.coverage.rows.toLocaleString('en-US')} rows read, covering ${new Date(p.coverage.first_ts * 1000).toISOString().slice(0, 10)} onwards. Positions opened before that are missing from the FIFO matching, so the totals are a lower bound on a window, not a record.`
              : `Polymarket's own all-time profit for this wallet is ${money(p.reconciliation.lb_profit)}, which falls outside our reconstruction's ${money(p.reconciliation.reconstructed)} … ${money(p.reconciliation.reconstructed_high)}. Something is unmodelled — most likely it exits by merge, which the activity feed does not publish. Read the shape below, not the totals.`}
          </span>
        </div>
      )}

      {/* ── the one-line version ── */}
      <p className="wallet-headline">
        <Rich text={p.narrative.headline} />
      </p>

      {/* ── archetypes ── */}
      <div className="wallet-chips">
        {p.archetypes.map((a) => (
          <span key={a.key} className={`wallet-chip conf-${a.confidence}`}>
            {a.label}
            <span className="wallet-chip-conf">{a.confidence}</span>
          </span>
        ))}
      </div>

      {/* ── headline numbers ── */}
      <div className="wallet-stats">
        <Stat label="DEPLOYED" value={money(t.deployed)} sub={`${t.fills.toLocaleString('en-US')} fills`} />
        <Stat label="P&L" value={money(t.pnl)} tone={t.pnl >= 0 ? 'green' : 'red'}
              sub={cov.unmatched_lots ? `up to ${money(t.pnl_high)}` : undefined} />
        <Stat label="YIELD" value={signed(t.yield_pct, 2)} tone={t.yield_pct >= 0 ? 'green' : 'red'}
              sub="on money at risk" />
        <Stat
          label="95% CI (BY EVENT)"
          value={boot.ci_lo === null ? '—' : `${signed(boot.ci_lo, 1)} … ${signed(boot.ci_hi, 1)}`}
          tone={boot.ci_lo !== null && boot.ci_lo > 0 ? 'green' : 'red'}
          sub={boot.ci_lo === null ? 'too few events' : boot.ci_lo > 0 ? 'clears zero' : 'contains zero'}
        />
        <Stat label="MEDIAN HOLD" value={dur(p.hold.median_min)} sub={`${p.hold.under_10min_pct.toFixed(0)}% ≤10 min`} />
        <Stat label="MEDIAN TICKET" value={money(t.median_ticket)} sub={`max ${money(t.max_ticket)}`} />
        <Stat label="PEAK OPEN BOOK" value={money(p.exposure.peak_cost_basis)}
              sub={`recycled ${p.exposure.turnover.toFixed(1)}×`} />
        <Stat label="EVENTS" value={cov.events.toLocaleString('en-US')}
              sub={`${cov.markets.toLocaleString('en-US')} markets`} />
      </div>

      {/* ── the written reading ── */}
      {p.narrative.sections.map((sec) => (
        <Section key={sec.title} title={sec.title}>
          <div className="wallet-prose">
            {sec.paragraphs.map((para, i) => (
              <p key={i} className={para.startsWith('⚠️') ? 'wallet-flag' : undefined}>
                <Rich text={para} />
              </p>
            ))}
          </div>
        </Section>
      ))}

      {/* ── entry price ── */}
      <Section title="By entry price" sub="where the return actually lives">
        <div className="wallet-table-wrap">
          <table className="wallet-table">
            <thead>
              <tr>
                <th>entry</th><th>lots</th><th>cost</th><th></th><th>P&amp;L</th><th>return</th><th>share of P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {p.entry_bands.filter((b) => b.lots > 0).map((b) => (
                <tr key={b.label}>
                  <td className="mono">{b.label}</td>
                  <td className="num">{b.lots.toLocaleString('en-US')}</td>
                  <td className="num">{money(b.cost)}</td>
                  <td className="barcell"><Bar value={b.cost} max={maxBandCost} /></td>
                  <td className={`num ${b.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(b.pnl)}</td>
                  <td className={`num ${b.return_pct >= 0 ? 'c-green' : 'c-red'}`}>{signed(b.return_pct, 0)}</td>
                  <td className="num">{b.share_of_pnl.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── timing ── */}
      <Section title="By entry time" sub="minutes after the listed kick-off">
        <div className="wallet-table-wrap">
          <table className="wallet-table">
            <thead>
              <tr><th>window</th><th>lots</th><th>capital</th><th>share</th><th>P&amp;L</th><th>return</th></tr>
            </thead>
            <tbody>
              {timingRows.map(([key, label]) => {
                const w = p.timing[key]
                if (!w || !w.lots) return null
                return (
                  <tr key={key}>
                    <td>{label}</td>
                    <td className="num">{w.lots.toLocaleString('en-US')}</td>
                    <td className="num">{money(w.cost)}</td>
                    <td className="num">{w.share_of_cost.toFixed(1)}%</td>
                    <td className={`num ${w.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(w.pnl)}</td>
                    <td className={`num ${w.return_pct >= 0 ? 'c-green' : 'c-red'}`}>{signed(w.return_pct)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── evolution ── */}
      <Section title="Month by month" sub="scale, price, and what it was doing">
        <div className="wallet-table-wrap">
          <table className="wallet-table">
            <thead>
              <tr>
                <th>month</th><th>events</th><th>deployed</th><th>P&amp;L</th><th></th>
                <th>yield</th><th>pre-match $</th><th>mean entry</th><th>median hold</th>
              </tr>
            </thead>
            <tbody>
              {p.months.map((m) => (
                <tr key={m.month} className={m.partial ? 'wallet-partial' : undefined}>
                  <td className="mono">
                    {m.month}
                    {m.partial ? <span className="wallet-tag">in progress</span> : null}
                  </td>
                  <td className="num">{m.events.toLocaleString('en-US')}</td>
                  <td className="num">{money(m.deployed)}</td>
                  <td className={`num ${m.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(m.pnl)}</td>
                  <td className="barcell"><Bar value={m.pnl} max={maxMonthPnl} /></td>
                  <td className={`num ${m.yield_pct >= 0 ? 'c-green' : 'c-red'}`}>{signed(m.yield_pct)}</td>
                  <td className="num">{m.prematch_share.toFixed(0)}%</td>
                  <td className="num">{m.mean_entry.toFixed(3)}</td>
                  <td className="num">{dur(m.median_hold_min)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── in-play move decomposition ── */}
      {p.moves.some((m) => m.lots > 0) && (
        <Section title="In-play, by how far the price moved" sub="entry → exit on positions opened during the match">
          <div className="wallet-table-wrap">
            <table className="wallet-table">
              <thead><tr><th>move</th><th>lots</th><th>cost</th><th>P&amp;L</th><th>return</th></tr></thead>
              <tbody>
                {p.moves.filter((m) => m.lots > 0).map((m) => (
                  <tr key={m.label}>
                    <td className="mono">{m.label}</td>
                    <td className="num">{m.lots.toLocaleString('en-US')}</td>
                    <td className="num">{money(m.cost)}</td>
                    <td className={`num ${m.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(m.pnl)}</td>
                    <td className={`num ${m.pnl >= 0 ? 'c-green' : 'c-red'}`}>
                      {m.cost ? signed((100 * m.pnl) / m.cost, 0) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* ── universe ── */}
      <Section title="Where it plays" sub="competition and market type">
        <div className="wallet-two">
          <div className="wallet-table-wrap">
            <table className="wallet-table">
              <thead><tr><th>competition</th><th>capital</th><th>P&amp;L</th><th>return</th></tr></thead>
              <tbody>
                {p.universe.slice(0, 10).map((u) => (
                  <tr key={u.label}>
                    <td>{u.label}</td>
                    <td className="num">{money(u.cost)}</td>
                    <td className={`num ${u.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(u.pnl)}</td>
                    <td className={`num ${u.return_pct >= 0 ? 'c-green' : 'c-red'}`}>{signed(u.return_pct, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="wallet-table-wrap">
            <table className="wallet-table">
              <thead><tr><th>market type</th><th>capital</th><th>P&amp;L</th><th>return</th></tr></thead>
              <tbody>
                {p.market_types.slice(0, 10).map((m) => (
                  <tr key={m.label}>
                    <td className="mono">{m.label}</td>
                    <td className="num">{money(m.cost)}</td>
                    <td className={`num ${m.pnl >= 0 ? 'c-green' : 'c-red'}`}>{money(m.pnl)}</td>
                    <td className={`num ${m.return_pct >= 0 ? 'c-green' : 'c-red'}`}>{signed(m.return_pct, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      {/* ── sweeps ── */}
      {p.sweeps.examples.length > 0 && (
        <Section
          title="Cheap in, repriced out"
          sub={`${p.sweeps.lots.toLocaleString('en-US')} lots · ${p.sweeps.share_of_pnl.toFixed(0)}% of P&L on ${p.sweeps.share_of_cost.toFixed(1)}% of capital`}
        >
          <div className="wallet-table-wrap">
            <table className="wallet-table">
              <thead><tr><th>market</th><th>shares</th><th>entry</th><th>exit</th><th>held</th><th>P&amp;L</th></tr></thead>
              <tbody>
                {p.sweeps.examples.map((e: any, i: number) => (
                  <tr key={i}>
                    <td className="wallet-mkt">{e.title} <span className="wallet-outcome">{e.outcome}</span></td>
                    <td className="num">{Math.round(e.shares).toLocaleString('en-US')}</td>
                    <td className="num mono">{e.entry.toFixed(3)}</td>
                    <td className="num mono">{e.exit.toFixed(2)}</td>
                    <td className="num">{e.hold_min === null ? 'held' : dur(e.hold_min)}</td>
                    <td className="num c-green">{money(e.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <footer className="wallet-foot">
        Reconstructed from Polymarket&rsquo;s public activity feed on {p.generated_at.slice(0, 10)}. FIFO
        round trips; unsold positions marked at settlement or last trade. This is a reading of one
        wallet&rsquo;s record, not advice and not a signal to follow.
      </footer>
    </div>
  )
}
