import { useEffect, type RefObject } from 'react'

const FOCUSABLE = 'button, input, select, textarea, [href], [tabindex]:not([tabindex="-1"])'

/**
 * The one keyboard contract for every layer that floats over the desk
 * (modal, drawer, off-canvas nav): focus moves in on open and back out on
 * close, Tab wraps inside the panel, Escape closes unless work is in flight.
 *
 * Document-level so the trap survives focus escaping the subtree (backdrop
 * clicks, controls disabling under a busy state).
 */
export function useFocusTrap({ open, panelRef, initialFocusRef, onEscape, busy = false }: {
  open: boolean
  panelRef: RefObject<HTMLElement>
  /** Where focus lands on open; the panel itself when absent. */
  initialFocusRef?: RefObject<HTMLElement>
  onEscape: () => void
  busy?: boolean
}) {
  useEffect(() => {
    if (!open) return
    const restore = document.activeElement as HTMLElement | null
    const target = initialFocusRef?.current ?? panelRef.current
    target?.focus()
    return () => {
      restore?.focus?.()
    }
  }, [open, panelRef, initialFocusRef])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        if (!busy) onEscape()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => !el.hasAttribute('disabled'))
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
  }, [open, busy, onEscape, panelRef])
}
