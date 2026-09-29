import { expect, test } from 'vitest'
import {
  depositFixture, destinationFixture, entryFixture, investorRowFixture, methodFixture, summaryFixture,
  transferFixture, withdrawalFixture,
} from './portalFixtures'

test('the summary carries the documented figures and every wallet', () => {
  const s = summaryFixture()
  expect(s.wallets.main).toEqual({ balance: 5120.5, on_hold: 100, available: 5020.5 })
  expect(Object.keys(s.wallets)).toEqual(['main', 'credit', 'pamm', 'social'])
  expect(s.currency).toBe('USD')
  expect(summaryFixture({ link_state: 'unlinked', account: null }).account).toBeNull()
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
