import { ReactNode, useEffect, useState } from 'react'

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

  useEffect(() => {
    if (!open) setTyped('')
  }, [open])

  if (!open) return null

  const blocked = Boolean(typeToConfirm) && typed !== typeToConfirm

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="w-full max-w-md rounded-lg bg-card shadow-xl border border-line">
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
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm text-ink"
              />
            </div>
          )}
        </div>
        <div className="px-6 py-4 flex justify-end gap-3 border-t border-line bg-paper rounded-b-lg">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium rounded border border-line-strong text-ink hover:bg-line/40 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={blocked || busy}
            className={`px-4 py-2 text-sm font-semibold rounded text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
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
