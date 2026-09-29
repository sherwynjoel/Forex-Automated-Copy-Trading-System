import type { ReactNode } from 'react'
import { usePageTitle } from '../hooks/usePageTitle'

/**
 * The one page heading: the single h1 (focus target after a route change),
 * an optional subtitle, actions on the right, and any toolbar as children.
 * Titles the document too.
 */
export default function PageHeader({ title, subtitle, actions, children }: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}) {
  usePageTitle(title)
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="page-title" tabIndex={-1} className="page-title outline-none">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-ink-soft">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </header>
  )
}
