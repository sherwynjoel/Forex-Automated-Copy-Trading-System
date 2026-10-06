import type { BadgeTone } from '../components/Badge'
import type { BonusSource, NotificationTopic, TicketStatus } from './types'

/** The notification topics, in the order the Settings page lists them. */
export const TOPICS: NotificationTopic[] = ['money', 'identity', 'support', 'bonus']

export const TOPIC_LABELS: Record<NotificationTopic, string> = {
  money: 'Money', identity: 'Identity', support: 'Support', bonus: 'Bonus',
}

/** What each email switch covers, as the Settings page names it. */
export const TOPIC_EMAIL_LABELS: Record<NotificationTopic, string> = {
  money: 'Money: deposits, withdrawals, transfers and adjustments',
  identity: 'Identity: verification and trading account requests',
  support: 'Support: ticket replies and closures',
  bonus: 'Bonus: bonuses paid or taken back',
}

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  new: 'New', open: 'Open', closed: 'Closed',
}

export const TICKET_STATUS_TONES: Record<TicketStatus, BadgeTone> = {
  new: 'warn', open: 'brand', closed: 'neutral',
}

export const BONUS_SOURCES: BonusSource[] = ['signup', 'kyc', 'deposit', 'manual']

export const BONUS_SOURCE_LABELS: Record<BonusSource, string> = {
  signup: 'Welcome', kyc: 'Verification', deposit: 'Deposit', manual: 'Manual',
}

/** Ticket images: the server sniffs the bytes and refuses anything else. */
export const IMAGE_ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
export const MAX_IMAGES = 3

/** The textarea look (the one PaymentMethodsTab and AccountRequestsTab
 *  already use); every phase 4 message box takes it from here. */
export const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

/** An in-app link from the server, or null for anything that is not one of
 *  our own paths: the column only ever holds paths, and this keeps a stray
 *  value from turning into an off-site navigation. */
export function safeLink(link: string | null): string | null {
  return link && link.startsWith('/') && !link.startsWith('//') ? link : null
}

/** A ticket list tail with only the filters that are set. */
export function ticketsQuery(base: string, status: TicketStatus | 'all', q: string): string {
  const p = new URLSearchParams()
  if (status !== 'all') p.set('status', status)
  if (q.trim()) p.set('q', q.trim())
  const s = p.toString()
  return s ? `${base}?${s}` : base
}
