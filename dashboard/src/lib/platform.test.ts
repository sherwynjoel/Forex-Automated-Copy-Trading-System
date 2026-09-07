import { expect, test } from 'vitest'
import { accountIdent, accountName, accountWho, isMt5, platformCaption } from './platform'
import { mt5Account } from '../test/mt5Fixtures'
import type { Account } from './types'

const ctrader: Account = {
  ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
  enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', nickname: null,
}

// Added a minute ago: no hello yet, so the api has no login to copy into
// trader_login and reports 0 there.
const fresh: Account = {
  ...mt5Account, trader_login: 0, connection_status: 'never',
  mt5: { ...mt5Account.mt5!, login: null, broker: null, server: null, connected: false, last_seen_at: null },
}

test('an account without a platform field is cTrader (older api)', () => {
  expect(isMt5(ctrader)).toBe(false)
  expect(isMt5({ platform: 'ctrader' })).toBe(false)
  expect(isMt5(mt5Account)).toBe(true)
})

test('the identifying figure is the trader login for cTrader, the MT5 login for MT5', () => {
  expect(accountIdent(ctrader)).toBe('12345')
  expect(accountIdent(mt5Account)).toBe('MT5 · login 555')
})

test('an MT5 account that has never reported says so rather than printing login 0', () => {
  expect(accountIdent(fresh)).toBe('MT5 · waiting for the terminal')
  expect(accountName({ ...fresh, nickname: null })).toBe('MT5 · waiting for the terminal')
})

test('accountName prefers the nickname, then the platform-aware ident', () => {
  expect(accountName({ ...ctrader, nickname: 'Main' })).toBe('Main')
  expect(accountName(ctrader)).toBe('Account 12345')
  expect(accountName({ ...mt5Account, nickname: null })).toBe('MT5 · login 555')
})

test('accountWho never repeats the login', () => {
  expect(accountWho({ ...ctrader, nickname: 'Second' })).toBe('Second · 12345')
  expect(accountWho(ctrader)).toBe('Account 12345')
  expect(accountWho(mt5Account)).toBe('VPS desk · MT5 · login 555')
  expect(accountWho({ ...mt5Account, nickname: null })).toBe('MT5 · login 555')
})

test('platformCaption lists the platforms present, cTrader first', () => {
  expect(platformCaption([ctrader])).toBe('cTrader')
  expect(platformCaption([mt5Account])).toBe('MT5')
  expect(platformCaption([mt5Account, ctrader])).toBe('cTrader · MT5')
  expect(platformCaption([])).toBe('cTrader · MT5')
})
