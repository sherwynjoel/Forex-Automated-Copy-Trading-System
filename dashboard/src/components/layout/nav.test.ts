import { expect, test } from 'vitest'
import { adminNav, bottomBarItems, investorNav } from './nav'

test('investorNav is three groups: the Dashboard alone, Money, and Account', () => {
  const groups = investorNav(7)
  expect(groups.map((g) => g.name)).toEqual(['', 'Money', 'Account'])
  expect(groups[0].items).toEqual([{ path: '/org/7/invest', label: 'Dashboard', end: true }])
  expect(groups[1].items.map((i) => [i.label, i.path])).toEqual([
    ['Wallet', '/org/7/invest/wallet'],
    ['Deposit', '/org/7/invest/deposit'],
    ['Withdraw', '/org/7/invest/withdraw'],
    ['Transfer', '/org/7/invest/transfer'],
    ['Transactions', '/org/7/invest/transactions'],
    ['Payout accounts', '/org/7/invest/payout-accounts'],
    ['Bonus', '/org/7/invest/bonus'],
  ])
  expect(groups[2].items.map((i) => [i.label, i.path])).toEqual([
    ['Trading account', '/org/7/invest/account'],
    ['Profile & verification', '/org/7/invest/profile'],
    ['Open account', '/org/7/invest/open-account'],
    ['Security', '/org/7/invest/security'],
    ['History', '/org/7/invest/history'],
    ['Support', '/org/7/invest/support'],
    ['Notifications', '/org/7/invest/notifications'],
    ['Settings', '/org/7/invest/settings'],
  ])
  expect(groups.flatMap((g) => g.items)).toHaveLength(16)
})

test('the investor tab bar is the four money-first pages, Dashboard exact-matched', () => {
  expect(bottomBarItems(7, 'investor')).toEqual([
    { path: '/org/7/invest', label: 'Dashboard', end: true },
    { path: '/org/7/invest/deposit', label: 'Deposit' },
    { path: '/org/7/invest/withdraw', label: 'Withdraw' },
    { path: '/org/7/invest/transactions', label: 'Transactions' },
  ])
  expect(bottomBarItems(7, 'admin').map((i) => i.label)).toEqual(['Overview', 'Positions', 'Trade', 'Accounts'])
  expect(bottomBarItems(7, 'viewer').map((i) => i.label)).toEqual(['Overview', 'Positions', 'History', 'Accounts'])
})

test('the admin Org group is Members, Investors, Requests, Logs; viewers get Members and Logs', () => {
  const org = adminNav(7, 'admin').find((g) => g.name === 'Org')!
  expect(org.items.map((i) => [i.label, i.path])).toEqual([
    ['Members', '/org/7/members'],
    ['Investors', '/org/7/investors'],
    ['Requests', '/org/7/requests'],
    ['Logs', '/org/7/logs'],
  ])
  const viewerOrg = adminNav(7, 'viewer').find((g) => g.name === 'Org')!
  expect(viewerOrg.items.map((i) => i.label)).toEqual(['Members', 'Logs'])
  expect(adminNav(7, 'admin').flatMap((g) => g.items)).toHaveLength(11)
})

test('the Requests item carries the open count it was given, and nothing else does', () => {
  const items = adminNav(7, 'admin', 3).flatMap((g) => g.items)
  expect(items.find((i) => i.label === 'Requests')?.badge).toBe(3)
  expect(items.filter((i) => i.badge !== undefined)).toHaveLength(1)
  expect(adminNav(7, 'admin').flatMap((g) => g.items).find((i) => i.label === 'Requests')?.badge).toBeUndefined()
})
