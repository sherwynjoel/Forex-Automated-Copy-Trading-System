import type { KeyboardEvent } from 'react'

export interface TabItem {
  key: string
  label: string
  count?: number
}

/**
 * The desk's one tab bar. Roving tabindex: only the selected tab is in the
 * tab order; ArrowLeft/ArrowRight wrap, Home/End jump. The caller renders
 * the panel with id `${idBase}-panel`, role="tabpanel" and
 * aria-labelledby=`${idBase}-tab-${value}`.
 */
export default function Tabs({ items, value, onChange, label, idBase }: {
  items: TabItem[]
  value: string
  onChange: (key: string) => void
  label: string
  idBase: string
}) {
  const select = (key: string) => {
    onChange(key)
    document.getElementById(`${idBase}-tab-${key}`)?.focus()
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = items.findIndex((t) => t.key === value)
    if (idx === -1) return
    let next: number | null = null
    if (e.key === 'ArrowRight') next = (idx + 1) % items.length
    else if (e.key === 'ArrowLeft') next = (idx + items.length - 1) % items.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    if (next == null) return
    e.preventDefault()
    select(items[next].key)
  }
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKeyDown}
         className="flex gap-1 overflow-x-auto border-b border-line">
      {items.map((t) => {
        const selected = t.key === value
        return (
          <button
            key={t.key}
            id={`${idBase}-tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`${idBase}-panel`}
            tabIndex={selected ? 0 : -1}
            onClick={() => select(t.key)}
            className={`min-h-11 md:min-h-0 whitespace-nowrap rounded-t-control border-b-2 px-4 py-2 text-sm transition-colors duration-150 ${
              selected ? 'border-brand text-brand font-semibold' : 'border-transparent text-ink-soft hover:text-ink'
            }`}
          >
            {t.label}
            {t.count != null && <span className="num ml-1 text-xs text-ink-faint">{t.count}</span>}
          </button>
        )
      })}
    </div>
  )
}
