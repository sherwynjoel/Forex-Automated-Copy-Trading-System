import type { ReactNode } from 'react'
import Card from '../../components/Card'
import { LANDING_FACTS } from '../Landing'

/**
 * A "not yet" state that says what happens next in one sentence, and who
 * to write to when the workspace has published a support address
 * (LANDING_FACTS.supportEmail, the same address the landing footer shows).
 * With no address the line is left out rather than showing an empty mailto.
 */
export default function NextStep({ title, children }: { title: string; children: ReactNode }) {
  const email = LANDING_FACTS.supportEmail
  return (
    <Card title={title}>
      <p className="text-sm text-ink-soft">{children}</p>
      {email && (
        <p className="text-sm text-ink-soft mt-2">
          Questions? Write to{' '}
          <a href={`mailto:${email}`}
             className="text-brand underline underline-offset-2 hover:text-brand-deep">
            {email}
          </a>
          .
        </p>
      )}
    </Card>
  )
}
