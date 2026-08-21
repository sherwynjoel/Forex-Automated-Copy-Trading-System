import { useEffect, useMemo, useRef, useState } from 'react'

export interface SearchSelectOption {
  value: string
  label: string
  /** Extra text the filter matches against (e.g. nickname, login, role). */
  keywords?: string
}

interface SearchSelectProps {
  id: string
  options: SearchSelectOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Render values in the desk's tabular mono (symbols, numbers). */
  mono?: boolean
  /** Shown when the filter (or the option list itself) comes up empty. */
  emptyMessage?: string
}

export default function SearchSelect({
  id, options, value, onChange, placeholder, mono = false, emptyMessage,
}: SearchSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [typed, setTyped] = useState(false)
  const [highlight, setHighlight] = useState(-1)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const selected = options.find((o) => o.value === value)
  const listboxId = `${id}-listbox`

  const filtered = useMemo(() => {
    if (!typed) return options
    const needle = query.trim().toLowerCase()
    if (!needle) return options
    return options.filter((o) =>
      `${o.label} ${o.keywords ?? ''}`.toLowerCase().includes(needle))
  }, [options, query, typed])

  const openList = () => {
    setOpen(true)
    setQuery(selected?.label ?? '')
    setTyped(false)
    setHighlight(-1)
  }

  const closeList = () => {
    setOpen(false)
    setTyped(false)
    setHighlight(-1)
  }

  const select = (option: SearchSelectOption) => {
    onChange(option.value)
    closeList()
  }

  // Close on any press outside the component.
  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) closeList()
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [open])

  // Keep the highlighted row visible while arrowing through a long list.
  useEffect(() => {
    if (highlight < 0) return
    const el = listRef.current?.children[highlight] as HTMLElement | undefined
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [highlight])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) return openList()
      setHighlight((h) => Math.min(h + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      if (open && highlight >= 0 && filtered[highlight]) {
        e.preventDefault()
        select(filtered[highlight])
      }
    } else if (e.key === 'Escape') {
      if (open) {
        e.stopPropagation()
        closeList()
      }
    }
  }

  return (
    <div ref={wrapperRef} className="relative">
      <input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={highlight >= 0 ? `${listboxId}-${highlight}` : undefined}
        autoComplete="off"
        placeholder={placeholder}
        value={open ? query : selected?.label ?? ''}
        onClick={() => { if (!open) openList() }}
        onFocus={() => { if (!open) openList() }}
        onBlur={closeList}
        onKeyDown={onKeyDown}
        onChange={(e) => {
          setQuery(e.target.value)
          setTyped(true)
          setHighlight(-1)
          if (!open) setOpen(true)
        }}
        className={`${mono ? 'num ' : ''}w-full rounded border border-line-strong px-3 py-2 pr-8 text-sm bg-card`}
      />
      <svg
        aria-hidden="true"
        viewBox="0 0 16 16"
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ink-soft"
      >
        <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
      {open && (
        <ul
          id={listboxId}
          ref={listRef}
          role="listbox"
          className="absolute z-10 mt-1 w-full max-h-60 overflow-auto rounded border border-line-strong bg-card shadow-sm"
        >
          {filtered.length === 0 && (
            <li className="px-3 py-2 text-sm text-ink-soft" aria-disabled="true">
              {emptyMessage ?? 'No matches'}
            </li>
          )}
          {filtered.map((option, index) => (
            <li
              key={option.value}
              id={`${listboxId}-${index}`}
              role="option"
              aria-selected={option.value === value}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => select(option)}
              onMouseEnter={() => setHighlight(index)}
              className={`${mono ? 'num ' : ''}px-3 py-2 text-sm cursor-pointer ${
                index === highlight ? 'bg-brand-wash text-ink' : 'text-ink'
              } ${option.value === value ? 'font-semibold text-brand' : ''}`}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
