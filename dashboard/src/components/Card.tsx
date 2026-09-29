import { useId, type ReactNode } from 'react'

/**
 * A glass panel: the desk's one card. The optional header carries the
 * section heading (h2) and its actions. `inset` renders the children on a
 * crisp opaque surface, which is where tables, forms and figures belong;
 * data never sits directly on glass.
 */
export default function Card({ title, actions, inset, className, as: Tag = 'section', children }: {
  title?: ReactNode
  actions?: ReactNode
  inset?: boolean
  className?: string
  as?: 'section' | 'div' | 'article'
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <Tag
      aria-labelledby={title ? headingId : undefined}
      className={['glass rounded-card shadow-card border p-5', className ?? ''].filter(Boolean).join(' ')}
    >
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          {title && <h2 id={headingId} className="text-lg font-semibold text-ink">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {inset ? <div className="inset overflow-hidden">{children}</div> : children}
    </Tag>
  )
}
