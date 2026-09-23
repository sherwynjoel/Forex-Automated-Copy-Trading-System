import { money } from './format'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

const LABELS: Record<string, string> = {
  pending: 'Pending review',
  confirmed: 'Confirmed',
  requested: 'Awaiting approval',
  approved: 'Approved, payment pending',
  paid: 'Paid',
  rejected: 'Rejected',
}

export function statusLabel(status: string): string {
  return LABELS[status] ?? status
}

export function statusTone(status: string): StatusTone {
  if (status === 'confirmed' || status === 'paid') return 'ok'
  if (status === 'pending' || status === 'requested' || status === 'approved') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'quiet'
}

export function moneyOrDash(n: number | null | undefined): string {
  return n == null ? '—' : money(n)
}

/** Tailwind classes for a status pill, matching Automation's OutcomePill. */
export function pillClass(status: string): string {
  const tone = statusTone(status)
  return tone === 'ok' ? 'bg-profit-wash text-profit-deep'
    : tone === 'warn' ? 'bg-warn-wash text-warn-deep'
    : tone === 'bad' ? 'bg-loss-wash text-loss-deep'
    : 'bg-paper text-ink-soft'
}
