import type { ReactNode } from 'react'

/**
 * The desk's one banner vocabulary. `error` announces via role="alert",
 * everything else via role="status"; pass onDismiss for page-local banners.
 * Pass announce={false} when the banner sits inside an always-mounted live
 * region of its own (a conditionally-mounted role="status" is unreliable).
 */
export default function Banner({ kind, children, onDismiss, announce = true }: {
  kind: 'error' | 'notice' | 'warn'
  children: ReactNode
  onDismiss?: () => void
  announce?: boolean
}) {
  const styles =
    kind === 'error' ? 'border-loss/30 bg-loss-wash text-loss-deep'
    : kind === 'warn' ? 'border-warn/30 bg-warn-wash text-warn-deep'
    : 'border-line bg-brand-wash text-ink'
  return (
    <div
      role={announce ? (kind === 'error' ? 'alert' : 'status') : undefined}
      className={`rounded border px-4 py-3 text-sm flex justify-between items-start gap-3 ${styles}`}
    >
      <div className="min-w-0">{children}</div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-xs font-semibold shrink-0 opacity-70 hover:opacity-100"
        >
          Dismiss
        </button>
      )}
    </div>
  )
}
