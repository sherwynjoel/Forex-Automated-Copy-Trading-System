import { useId, useRef, type ReactNode } from 'react'
import Button from './Button'
import { useFocusTrap } from './useFocusTrap'

/**
 * The desk's side panel: a glass layer that slides over the page for
 * details and secondary work (account details, a position's deals, the
 * phone navigation). One keyboard contract everywhere: focus lands on
 * Close, Tab wraps, Escape and the backdrop close it, focus returns to the
 * control that opened it.
 */
export default function Drawer({ open, title, onClose, children, headerExtra, busy = false }: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  /** Sits beside the title: a platform badge, a status dot. */
  headerExtra?: ReactNode
  /** Work in flight: Escape and the backdrop are ignored until it finishes. */
  busy?: boolean
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()

  useFocusTrap({ open, panelRef, initialFocusRef: closeRef, onEscape: onClose, busy })

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div
        data-testid="drawer-backdrop"
        className="absolute inset-0 bg-black/30 backdrop-blur-[2px]"
        onClick={() => { if (!busy) onClose() }}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="glass relative h-full w-full max-w-md flex flex-col shadow-xl outline-none"
      >
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-line">
          <div className="flex items-center gap-2 min-w-0">
            <h2 id={titleId} className="font-display text-lg text-ink truncate">{title}</h2>
            {headerExtra}
          </div>
          <Button ref={closeRef} variant="ghost" size="sm" aria-label="Close" onClick={onClose}>
            <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
              <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  )
}
