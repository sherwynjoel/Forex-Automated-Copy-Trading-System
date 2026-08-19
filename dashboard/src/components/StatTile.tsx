import type { ReactNode } from 'react'

/** The desk's stat tile: small-caps label, bold tabular figure, quiet sub. */
export default function StatTile({ label, value, tone, sub }: {
  label: string
  value: string
  tone?: 'profit' | 'loss' | 'brand' | 'neutral'
  sub?: ReactNode
}) {
  const toneClass =
    tone === 'profit' ? 'text-profit'
    : tone === 'loss' ? 'text-loss'
    : tone === 'brand' ? 'text-brand'
    : 'text-ink'
  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="desk-label">{label}</div>
      <div className={`num text-2xl font-semibold tracking-tight mt-1 ${toneClass}`}>{value}</div>
      {sub != null && sub !== '' && <div className="text-xs text-ink-soft mt-0.5">{sub}</div>}
    </div>
  )
}
