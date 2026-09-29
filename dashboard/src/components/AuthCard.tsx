import type { ReactNode } from 'react'
import Logo from './Logo'
import { usePageTitle } from '../hooks/usePageTitle'

/**
 * The glass card every auth screen lives in: logo, a real h1 naming the
 * step, an optional lead, the form, an optional footer. It is the page's
 * main region, so the skip link and assistive tech land on it.
 */
export default function AuthCard({ title, lead, children, footer }: {
  title: string
  lead?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  usePageTitle(title)
  return (
    <main id="main" className="min-h-screen flex items-center justify-center p-4">
      <div className="glass rounded-card shadow-float border w-full max-w-md p-8">
        <div className="mb-6 flex justify-center"><Logo size={28} /></div>
        <h1 id="page-title" tabIndex={-1} className="page-title text-center">{title}</h1>
        {lead && <p className="mt-2 text-center text-sm text-ink-soft">{lead}</p>}
        <div className="mt-6">{children}</div>
        {footer && <div className="mt-6 text-center text-sm text-ink-soft">{footer}</div>}
      </div>
    </main>
  )
}
