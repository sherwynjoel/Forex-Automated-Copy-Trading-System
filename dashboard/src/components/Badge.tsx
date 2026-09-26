import type { ReactNode } from 'react'

export type BadgeTone = 'brand' | 'profit' | 'loss' | 'warn' | 'neutral'

/**
 * The desk's one chip: wash background with the matching deep text, the
 * exact pairs the palette prover gates in both themes. Meaning comes from
 * the words inside it; the tone only echoes them.
 */
const TONE: Record<BadgeTone, string> = {
  brand: 'bg-brand-wash text-brand-deep',
  profit: 'bg-profit-wash text-profit-deep',
  loss: 'bg-loss-wash text-loss-deep',
  warn: 'bg-warn-wash text-warn-deep',
  neutral: 'bg-line text-ink-soft',
}

export default function Badge({ tone, pill, className, children }: {
  tone: BadgeTone
  /** Fully rounded, for status pills such as DRY RUN. */
  pill?: boolean
  className?: string
  children: ReactNode
}) {
  const classes = [
    'inline-flex items-center px-2 py-0.5 text-xs font-semibold',
    pill ? 'rounded-full' : 'rounded',
    TONE[tone],
    className ?? '',
  ].filter(Boolean).join(' ')
  return <span className={classes}>{children}</span>
}
