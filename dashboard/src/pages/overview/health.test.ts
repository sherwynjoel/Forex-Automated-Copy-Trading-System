import { expect, test } from 'vitest'
import type { Account } from '../../lib/types'
import { accountHealth } from './health'

const base: Account = {
  ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
  enabled: true, multiplier: 1, status: 'ok', last_error: null,
  connection_status: 'active', nickname: null,
}
const at = (over: Partial<Account>) => accountHealth({ ...base, ...over })

test.each([
  ['ok', {}, 'OK'],
  ['ok', { status: 'connected' }, 'OK'],
  ['paused', { status: 'paused' }, 'Paused'],
  ['paused', { enabled: false }, 'Paused'],
  ['degraded', { status: 'degraded' }, 'Degraded'],
  ['disconnected', { status: 'disconnected' }, 'Disconnected'],
  ['offline', { connection_status: 'offline' }, 'Terminal offline'],
  // One fault, its most severe name: an offline terminal is also marked
  // degraded by the copier, and a broken account that is also paused is
  // still broken.
  ['offline', { connection_status: 'offline', status: 'degraded' }, 'Terminal offline'],
  ['degraded', { status: 'degraded', enabled: false }, 'Degraded'],
] as const)('%s <- %j', (level, over, label) => {
  expect(at(over)).toEqual({ level, label })
})

test('a paused master is not called degraded', () => {
  expect(at({ status: 'paused' }).level).not.toBe('degraded')
})
