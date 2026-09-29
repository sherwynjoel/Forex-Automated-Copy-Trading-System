import type { Account } from '../../lib/types'

export type HealthLevel = 'ok' | 'paused' | 'offline' | 'degraded' | 'disconnected'

/**
 * One account's health, the one reading the Overview uses everywhere: the
 * master card and the Attention list must never disagree. The most severe
 * fault wins -- an offline MT5 terminal is also marked degraded by the
 * copier, and a broken account that is also paused is still broken.
 * `paused` covers both a per-account Pause (status 'paused') and a
 * disabled account.
 */
export function accountHealth(account: Account): { level: HealthLevel; label: string } {
  if (account.connection_status === 'offline') return { level: 'offline', label: 'Terminal offline' }
  if (account.status === 'degraded') return { level: 'degraded', label: 'Degraded' }
  if (account.status === 'disconnected') return { level: 'disconnected', label: 'Disconnected' }
  if (account.status === 'paused' || !account.enabled) return { level: 'paused', label: 'Paused' }
  return { level: 'ok', label: 'OK' }
}
