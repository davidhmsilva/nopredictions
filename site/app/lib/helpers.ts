export function fmt(n: number, decimals = 1): string {
  return n.toFixed(decimals)
}

export function fmtPct(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`
}

export function fmtPnl(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n, 2)}u`
}

export function fmtClv(n: number): string {
  return `${n >= 0 ? '+' : ''}${fmt(n, 1)}¢`
}

export function colorClass(n: number): string {
  return n >= 0 ? 'text-green' : 'text-red'
}

export function resultBadgeClass(result: string | null): string {
  switch (result) {
    case 'won':
      return 'badge badge-live'
    case 'lost':
      return 'badge badge-rejected'
    default:
      return 'badge badge-pending'
  }
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
    + ' · ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
