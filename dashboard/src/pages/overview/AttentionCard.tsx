import type { ReactNode } from 'react'
import Card from '../../components/Card'
import StatusDot from '../../components/StatusDot'

export interface AttentionItem {
  key: string
  /** degraded = act now (red dot); warn = copying is held up (amber dot). */
  tone: 'degraded' | 'warn'
  /** One plain sentence. */
  message: ReactNode
  /** The one thing to do about it: a Button, as a link or an action. */
  action: ReactNode
  testId?: string
}

/**
 * Only live problems, each with its action. When nothing is wrong it says so
 * in one line naming the real copying state, and nothing else. The live
 * region is always mounted: a conditionally mounted role="status" is not
 * announced reliably.
 */
export default function AttentionCard({ items, calmState }: {
  items: AttentionItem[]
  /** "copying live", "copying paused", "dry run, copies are simulated", ... */
  calmState: string
}) {
  return (
    <div data-testid="attention-card">
      <Card as="section" title="Attention">
        <div role="status">
          {items.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-ink">
              <StatusDot tone="ok" />
              All clear — {calmState}
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {items.map((item) => (
                <li
                  key={item.key}
                  data-testid={item.testId}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0"
                >
                  <StatusDot tone={item.tone} />
                  <p className="min-w-0 flex-1 basis-60 text-sm text-ink">{item.message}</p>
                  <div className="shrink-0">{item.action}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  )
}
