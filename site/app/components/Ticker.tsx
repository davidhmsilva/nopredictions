const TICKER_ITEMS = [
  { label: 'PRIMARY VENUE', value: 'POLYMARKET', up: true },
  { label: 'SHARP BENCHMARK', value: 'BETFAIR EXCHANGE', up: true },
  { label: 'HISTORICAL MEMORY', value: '16 YEARS', up: true },
  { label: 'MATCHES IN DATABASE', value: '124,000+', up: true },
  { label: 'ODDS RECORDS', value: '393,458', up: true },
  { label: 'LEAGUES TRACKED', value: '22', up: true },
  { label: 'POLYMARKET MARKETS', value: 'LIVE', up: true },
  { label: 'PINNACLE CLOSING ODDS', value: 'LOADED', up: true },
  { label: 'AGENT STATUS', value: 'ACTIVE', up: true },
  { label: 'HYPOTHESES TESTED', value: '7 · 0 PROMOTED', up: true },
  { label: 'IN-PLAY MONITOR', value: 'LIVE', up: true },
  { label: 'NARRATIVE', value: 'AI · PREDICTION MARKETS', up: true },
]

export function Ticker() {
  return (
    <div className="ticker-bar">
      <div className="ticker-inner">
        {[...TICKER_ITEMS, ...TICKER_ITEMS].map((item, i) => (
          <span key={i} className={`ticker-item${item.up ? '' : ' down'}`}>
            {item.label} <b>{item.value}</b>
          </span>
        ))}
      </div>
    </div>
  )
}
