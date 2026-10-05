import { expect, test } from 'vitest'
import {
  accountSummaryFixture, depositFixture, destinationFixture, entryFixture, investorRowFixture, methodFixture,
  summaryFixture, transferFixture, withdrawalFixture,
} from './portalFixtures'

test('the summary carries the documented figures and every wallet', () => {
  const s = summaryFixture()
  expect(s.wallets.main).toEqual({ balance: 5120.5, on_hold: 100, available: 5020.5 })
  expect(Object.keys(s.wallets)).toEqual(['main', 'credit', 'pamm', 'social'])
  expect(s.currency).toBe('USD')
})

test('every row builder merges overrides over a complete row', () => {
  expect(depositFixture({ status: 'confirmed', credited_amount: 247.5 }).credited_amount).toBe(247.5)
  expect(withdrawalFixture().net_amount).toBe(247.5)
  expect(transferFixture().source).toEqual({ kind: 'wallet', wallet: 'main' })
  expect(destinationFixture().summary).toBe('ICICI Bank ••4543')
  expect(entryFixture({ amount: -250, kind: 'withdrawal' }).amount).toBe(-250)
  expect(methodFixture({ kind: 'bank', label: 'ICICI Bank' }).label).toBe('ICICI Bank')
  expect(investorRowFixture().available).toBe(5020.5)
})

test('the summary carries one account and the cap; the investor row its accounts', () => {
  const s = summaryFixture()
  expect(s.accounts.map((a) => a.account_id)).toEqual([555])
  expect(s.account_limit).toEqual({ max: 5, used: 1 })
  expect(accountSummaryFixture({ mt5_login: null }).mt5_login).toBeNull()
  expect(investorRowFixture().accounts).toEqual([
    { account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }])
})
