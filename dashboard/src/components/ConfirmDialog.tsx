import { ReactNode, useEffect, useId, useRef, useState } from 'react'
import Button from './Button'
import Input from './Input'
import { useFocusTrap } from './useFocusTrap'

interface ConfirmDialogProps {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  /** When set, the confirm button stays disabled until this exact phrase is typed. */
  typeToConfirm?: string
  busy?: boolean
  /** Confirm is refused for a reason that is NOT work in progress (e.g.
   * the form holds a value the broker would reject). Distinct from `busy`
   * so the button stays labelled rather than claiming to be working. */
  disabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Modal confirmation for actions that trade or destroy. The optional
 * type-to-confirm gate is reserved for the widest-blast-radius action
 * (closing every position everywhere); ordinary confirms just read and click.
 *
 * Focus lands on Cancel so a stray Enter cannot confirm blind; Tab wraps
 * inside the panel; Escape cancels unless work is in flight; on close focus
 * returns to whichever control opened the dialog (useFocusTrap). The desk
 * behind is frosted so the eye has one place to be.
 */
export default function ConfirmDialog({
  open, title, children, confirmLabel, danger, typeToConfirm, busy, disabled,
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()
  const phraseId = useId()

  useEffect(() => {
    if (!open) setTyped('')
  }, [open])

  useFocusTrap({ open, panelRef, initialFocusRef: cancelRef, onEscape: onCancel, busy })

  if (!open) return null

  const blocked = Boolean(typeToConfirm) && typed !== typeToConfirm

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div ref={panelRef} tabIndex={-1} className="w-full max-w-md rounded-lg bg-card shadow-xl border border-line outline-none">
        <div className="px-6 pt-5 pb-4 border-b border-line">
          <h2 id={titleId} className="font-display text-lg text-ink">{title}</h2>
        </div>
        <div className="px-6 py-4 text-sm text-ink-soft space-y-3">
          {children}
          {typeToConfirm && (
            <div>
              <label className="desk-label block mb-1" htmlFor={phraseId}>
                Type {typeToConfirm} to continue
              </label>
              <Input
                id={phraseId}
                type="text"
                num
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
              />
            </div>
          )}
        </div>
        <div className="px-6 py-4 flex justify-end gap-3 border-t border-line bg-paper rounded-b-lg">
          <Button ref={cancelRef} variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            tone={danger ? 'loss' : 'brand'}
            onClick={onConfirm}
            disabled={blocked || busy || disabled}
            aria-busy={busy || undefined}
          >
            {busy ? 'Working…' : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
