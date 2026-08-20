import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

/** The desk's stat tile: small-caps label, bold tabular figure, quiet sub.
 *  Pass `to` for a drill-down link, or `onClick`+`expanded` for an
 *  in-place disclosure toggle. */
export default function StatTile({ label, value, tone, sub, to, onClick, expanded }: {
  label: string
  value: string
  tone?: 'profit' | 'loss' | 'brand' | 'neutral'
  sub?: ReactNode
  to?: string
  onClick?: () => void
  expanded?: boolean
}) {
  const toneClass =
    tone === 'profit' ? 'text-profit'
    : tone === 'loss' ? 'text-loss'
    : tone === 'brand' ? 'text-brand'
    : 'text-ink'
  const interactive = Boolean(to || onClick)
  const body = (
    <>
      <div className="desk-label flex items-center justify-between gap-2">
        {label}
        {interactive && (
          <svg
            aria-hidden="true"
            viewBox="0 0 16 16"
            className={`h-3 w-3 text-ink-faint transition-transform ${expanded ? 'rotate-90' : ''}`}
          >
            <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>
      <div className={`num text-2xl font-semibold tracking-tight mt-1 ${toneClass}`}>{value}</div>
      {sub != null && sub !== '' && <div className="text-xs text-ink-soft mt-0.5">{sub}</div>}
    </>
  )
  const frame = 'rounded-lg border border-line bg-card p-4'
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-expanded={expanded ?? false}
        className={`${frame} block w-full text-left transition-colors hover:border-brand`}
      >
        {body}
      </button>
    )
  }
  if (to) {
    return (
      <Link to={to} className={`${frame} block transition-colors hover:border-brand`}>
        {body}
      </Link>
    )
  }
  return <div className={frame}>{body}</div>
}
