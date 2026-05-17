import type { PaperTrade } from '../lib/supabase'

const NARRATIVE_ITEMS = [
  { label: 'NO PREDICTIONS', value: 'JUST EDGES' },
  { label: 'AI VS', value: 'THE MARKET' },
  { label: 'EVERY PICK', value: 'LOGGED PUBLICLY' },
  { label: 'WINS AND LOSSES', value: 'ALL ON RECORD' },
  { label: 'FOOTBALL', value: 'PREDICTION MARKETS' },
  { label: 'TRAINED ON', value: '100,000+ MATCHES' },
]

export function Ticker({ trades }: { trades?: PaperTrade[] }) {
  const settled = trades?.filter(t => !!t.resolved_at) ?? []
  const open = trades?.filter(t => !t.resolved_at) ?? []
  const wins = settled.filter(t => t.result === 'won').length
  const losses = settled.length - wins
  const totalPnl = settled.reduce((s, t) => s + Number(t.payout_units ?? 0) - Number(t.stake_units ?? 0), 0)
  const pnlStr = settled.length > 0
    ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u`
    : '—'

  const liveItems = trades && trades.length > 0 ? [
    { label: 'OPEN POSITIONS', value: String(open.length) },
    { label: 'P&L', value: pnlStr },
    { label: 'RECORD', value: settled.length > 0 ? `${wins}W / ${losses}L` : '—' },
    { label: 'AGENT', value: 'SCANNING' },
  ] : [
    { label: 'AGENT', value: 'SCANNING' },
  ]

  const items = [...liveItems, ...NARRATIVE_ITEMS]

  return (
    <div className="ticker-bar">
      <div className="ticker-inner">
        {[...items, ...items].map((item, i) => (
          <span key={i} className="ticker-item">
            {item.label} <b>{item.value}</b>
          </span>
        ))}
      </div>
    </div>
  )
}
