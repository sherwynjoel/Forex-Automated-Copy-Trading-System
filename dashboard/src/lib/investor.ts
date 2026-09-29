import { money } from './format'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

/**
 * The unit for account-currency figures: equity, profit, available, trade
 * P&L. The investor summary carries NO currency field -- `summary_json` in
 * api/src/api/investor_ledger.py returns bare numbers for total_deposited,
 * total_withdrawn, net_deposits, pending_withdrawn, equity, profit and
 * available -- so this is a labelled default, not something the API said.
 * Ledger figures (deposits, withdrawals) use the wallet's coin instead.
 */
export const ACCOUNT_CURRENCY = 'USD'

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

export function moneyOrDash(n: number | null | undefined, unit?: string): string {
  return n == null ? '—' : money(n, unit)
}

/**
 * "T…9f": the first character and the last two, for a one-line summary
 * such as the Withdraw review title. The full address is always shown next
 * to it; short strings stay whole.
 */
export function shortAddress(address: string): string {
  const a = address.trim()
  return a.length <= 8 ? a : `${a.slice(0, 1)}…${a.slice(-2)}`
}

/** Tailwind classes for a status pill, matching Automation's OutcomePill. */
export function pillClass(status: string): string {
  const tone = statusTone(status)
  return tone === 'ok' ? 'bg-profit-wash text-profit-deep'
    : tone === 'warn' ? 'bg-warn-wash text-warn-deep'
    : tone === 'bad' ? 'bg-loss-wash text-loss-deep'
    : 'bg-paper text-ink-soft'
}
