import type { BadgeTone } from '../components/Badge'
import { money } from './format'
import type { WalletEntry, WalletKind } from './types'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

/**
 * The unit of every wallet and account figure in phase 1: the portal is
 * USD-only (spec section 3). Every API row and summary also carries a
 * `currency` field; pages prefer the row's value and fall back to this.
 */
export const ACCOUNT_CURRENCY = 'USD'

/** The four wallets in display order. Only `main` moves in phase 1. */
export const WALLETS: WalletKind[] = ['main', 'credit', 'pamm', 'social']

const WALLET_LABELS: Record<WalletKind, string> = {
  main: 'My wallet',
  credit: 'Credit wallet',
  pamm: 'PAMM wallet',
  social: 'Social wallet',
}

export function walletLabel(kind: WalletKind): string {
  return WALLET_LABELS[kind]
}

/** Which request table a status belongs to; only `approved` reads differently per table. */
export type RequestKind = 'deposit' | 'withdrawal' | 'transfer' | 'destination'

/** An approved withdrawal still has to be paid; an approved transfer is
 *  being funded at the broker; an approved payout account is just usable. */
export function approvedLabel(kind: RequestKind): string {
  if (kind === 'transfer') return 'Approved, in progress'
  if (kind === 'destination') return 'Approved'
  return 'Approved, payment pending'
}

const LABELS: Record<string, string> = {
  pending: 'Pending review',
  confirmed: 'Confirmed',
  requested: 'Awaiting approval',
  approved: 'Approved, payment pending',
  paid: 'Paid',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  done: 'Done',
  removed: 'Removed',
}

export function statusLabel(status: string, kind?: RequestKind): string {
  if (status === 'approved' && kind) return approvedLabel(kind)
  return LABELS[status] ?? status
}

export function statusTone(status: string, kind?: RequestKind): StatusTone {
  if (status === 'confirmed' || status === 'paid' || status === 'done') return 'ok'
  if (status === 'approved') return kind === 'destination' ? 'ok' : 'warn'
  if (status === 'pending' || status === 'requested') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'quiet'
}

/** The status tones on the desk's one chip. */
export const BADGE_TONE: Record<StatusTone, BadgeTone> = {
  ok: 'profit',
  warn: 'warn',
  bad: 'loss',
  quiet: 'neutral',
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

const ENTRY_KINDS: Record<WalletEntry['kind'], string> = {
  deposit: 'Deposit',
  withdrawal: 'Withdrawal',
  transfer: 'Transfer',
  adjustment: 'Adjustment',
  bonus: 'Bonus',
  commission: 'Commission',
  fee: 'Fee',
}

/** "Deposit #12", "Withdrawal #4", "Transfer #9", "Adjustment" -- what a ledger row was for. */
export function entryLabel(e: WalletEntry): string {
  const word = ENTRY_KINDS[e.kind] ?? e.kind
  return e.ref_id != null ? `${word} #${e.ref_id}` : word
}
