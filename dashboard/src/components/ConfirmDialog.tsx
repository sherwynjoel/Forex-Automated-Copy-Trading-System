import { ReactNode, useEffect, useRef, useState } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  /** When set, the confirm button stays disabled until this exact phrase is typed. */
  typeToConfirm?: string
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Modal confirmation for actions that trade or destroy. The optional
 * type-to-confirm gate is reserved for the widest-blast-radius action
 * (closing every position everywhere); ordinary confirms just read and click.
 */
export default function ConfirmDialog({
  open, title, children, confirmLabel, danger, typeToConfirm, busy,
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) {
      setTyped('')
      return
    }
    // Focus lands on Cancel so a stray Enter cannot confirm blind; on close
    // it returns to whichever control opened the dialog.
    restoreRef.current = document.activeElement as HTMLElement | null
    cancelRef.current?.focus()
    return () => {
      restoreRef.current?.focus?.()
    }
  }, [open])

  // Document-level so the trap survives focus escaping the subtree (backdrop
  // clicks, buttons disabling under the busy state).
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        if (!busy) onCancel()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>('button, input, [href]')
      ).filter((el) => !el.hasAttribute('disabled'))
      if (focusables.length === 0) {
        // Everything is disabled (busy): keep focus parked on the panel.
        e.preventDefault()
        panel.focus()
        return
      }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (!panel.contains(active)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handler, true)
    return () => document.removeEventListener('keydown', handler, true)
  }, [open, busy, onCancel])

  if (!open) return null

  const blocked = Boolean(typeToConfirm) && typed !== typeToConfirm

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div ref={panelRef} tabIndex={-1} className="w-full max-w-md rounded-lg bg-card shadow-xl border border-line outline-none">
        <div className="px-6 pt-5 pb-4 border-b border-line">
          <h2 className="font-display text-lg text-ink">{title}</h2>
        </div>
        <div className="px-6 py-4 text-sm text-ink-soft space-y-3">
          {children}
          {typeToConfirm && (
            <div>
              <label className="desk-label block mb-1" htmlFor="confirm-phrase">
                Type {typeToConfirm} to continue
              </label>
              <input
                id="confirm-phrase"
                type="text"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm text-ink bg-card"
              />
            </div>
          )}
        </div>
        <div className="px-6 py-4 flex justify-end gap-3 border-t border-line bg-paper rounded-b-lg">
          <button
            ref={cancelRef}
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium rounded border border-line-strong text-ink hover:bg-line transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={blocked || busy}
            className={`px-4 py-2 text-sm font-semibold rounded text-on-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              danger ? 'bg-loss hover:bg-loss-deep' : 'bg-brand hover:bg-brand-deep'
            }`}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
