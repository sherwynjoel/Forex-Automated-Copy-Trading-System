import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
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
 */
export default function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0)
  const focusItem = (i: number) => {
    const el = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[i]
    el?.focus()
  }
  const close = () => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    focusItem(enabled[0] ?? 0)
    const onDocClick = (e: MouseEvent) => {
      if (!listRef.current?.contains(e.target as Node) && e.target !== triggerRef.current) setOpen(false)
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
    else if (e.key === 'Tab') { setOpen(false) }
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
        onClick={() => setOpen((o) => !o)}
        className="h-11 w-11 md:h-8 md:w-8 justify-center"
      >
        <span aria-hidden="true" className="text-lg leading-none">⋯</span>
      </Button>
      {open && (
        <div
          ref={listRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onKeyDown}
          className="glass absolute right-0 z-10 mt-1 min-w-44 rounded-inset border p-1 shadow-float"
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
        </div>
      )}
    </div>
  )
}
