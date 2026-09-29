import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import Button from './Button'

export interface MenuItem {
  key: string
  label: string
  tone?: 'neutral' | 'loss'
  disabled?: boolean
  onSelect: () => void
}

/**
 * The row-actions menu: one "⋯" trigger, a glass popover with role="menu".
 * Focus lands on the first enabled item, arrows move (skipping disabled
 * items), Enter/Space select, Escape and outside clicks close, and focus
 * returns to the trigger.
 *
 * The popover is portalled to <body> and positioned `fixed` from the
 * trigger's rect, so a scrolling or clipping ancestor (the Accounts table
 * sits in `overflow-x-auto` inside a backdrop-filtered glass card) can never
 * cut it off. It opens upward when there is no room below, and any scroll or
 * resize closes it rather than let it drift away from its row.
 */
export default function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const [pos, setPos] = useState<CSSProperties | null>(null)

  const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0)
  const focusItem = (i: number) => {
    const el = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[i]
    // The popover is fixed; focusing it must never scroll the page (a
    // scroll closes the menu).
    el?.focus({ preventScroll: true })
  }
  const close = () => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  // Right edge on the trigger's right edge, below it unless that would run
  // past the viewport's bottom margin. Before the popover exists its height
  // is estimated from the row count (jsdom always reports 0).
  const place = (): CSSProperties | null => {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return null
    const menuHeight = listRef.current?.offsetHeight || items.length * 36 + 8
    const right = window.innerWidth - rect.right
    return rect.bottom + menuHeight > window.innerHeight - 8
      ? { position: 'fixed', right, bottom: window.innerHeight - rect.top + 4 }
      : { position: 'fixed', right, top: rect.bottom + 4 }
  }

  // Re-place with the measured height before paint.
  useLayoutEffect(() => {
    if (open) setPos(place())
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return
    const dismiss = () => {
      // Keep focus somewhere sensible without scrolling the page again.
      if (listRef.current?.contains(document.activeElement)) triggerRef.current?.focus({ preventScroll: true })
      setOpen(false)
    }
    window.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      window.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    focusItem(enabled[0] ?? 0)
    const onDocClick = (e: MouseEvent) => {
      if (!listRef.current?.contains(e.target as Node) && !triggerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []
    ).findIndex((el) => el === document.activeElement)
    const pos = enabled.indexOf(current)
    // Clamp at the ends rather than wrap: Home/End cover the jump, and this
    // is what keeps two ArrowDowns from a middle disabled item landing back
    // where they started.
    if (e.key === 'ArrowDown') { e.preventDefault(); focusItem(enabled[Math.min(pos + 1, enabled.length - 1)]) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); focusItem(enabled[Math.max(pos - 1, 0)]) }
    else if (e.key === 'Home') { e.preventDefault(); focusItem(enabled[0]) }
    else if (e.key === 'End') { e.preventDefault(); focusItem(enabled[enabled.length - 1]) }
    else if (e.key === 'Escape') { e.preventDefault(); close() }
    // The popover lives at the end of <body>: hand focus back to the trigger
    // first so the browser's Tab moves on from the row, not from the page end.
    else if (e.key === 'Tab') { triggerRef.current?.focus({ preventScroll: true }); setOpen(false) }
  }

  return (
    <div className="relative inline-block">
      <Button
        ref={triggerRef}
        variant="ghost" tone="neutral" size="sm"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          if (!open) setPos(place())
          setOpen(!open)
        }}
        className="h-11 w-11 md:h-8 md:w-8 justify-center"
      >
        <span aria-hidden="true" className="text-lg leading-none">⋯</span>
      </Button>
      {open && createPortal(
        <div
          ref={listRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onKeyDown}
          style={pos ?? { position: 'fixed' }}
          className="glass fixed z-40 min-w-44 rounded-inset border p-1 shadow-float"
        >
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              role="menuitem"
              aria-disabled={it.disabled || undefined}
              tabIndex={-1}
              onClick={() => { if (it.disabled) return; setOpen(false); triggerRef.current?.focus(); it.onSelect() }}
              className={`block w-full rounded-control px-3 py-2 text-left text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                it.disabled ? 'text-ink-faint cursor-not-allowed'
                : it.tone === 'loss' ? 'text-loss hover:bg-loss-wash focus:bg-loss-wash'
                : 'text-ink hover:bg-brand-wash focus:bg-brand-wash'
              }`}
            >
              {it.label}
            </button>
          ))}
        </div>,
        document.body
      )}
    </div>
  )
}
