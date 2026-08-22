import { expect, test } from 'vitest'
import { mergeTicksIntoApiState, mergeTicksIntoSnapshot } from './ticks'
import type { ApiState, StateSnapshot } from './types'

const snapshot: StateSnapshot = {
  '100': {
    balance: 1000,
    equity: 1004.2,
    open_pnl: 4.2,
    positions: [
      { position_id: 7001, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
        volume: 200000, entry_price: 1.105, pnl_quote: 4.2, current_price: 1.10521 },
    ],
  },
}

test('a tick updates marks, P&L and equity for a known position', () => {
  const merged = mergeTicksIntoSnapshot(snapshot, {
    '100': {
      equity: 1009.9,
      open_pnl: 9.9,
      positions: [{ position_id: 7001, current_price: 1.10621, pnl_quote: 9.9 }],
    },
  })
  const acct = merged['100']
  expect(acct.equity).toBe(1009.9)
  expect(acct.open_pnl).toBe(9.9)
  expect(acct.positions[0].current_price).toBe(1.10621)
  expect(acct.positions[0].pnl_quote).toBe(9.9)
  // Untouched fields survive.
  expect(acct.balance).toBe(1000)
  expect(acct.positions[0].entry_price).toBe(1.105)
})

test('ticks never invent accounts or positions', () => {
  const merged = mergeTicksIntoSnapshot(snapshot, {
    '999': { positions: [{ position_id: 1, current_price: 5 }] },
  })
  expect(merged).toBe(snapshot) // same reference: nothing matched
  const merged2 = mergeTicksIntoSnapshot(snapshot, {
    '100': { positions: [{ position_id: 424242, current_price: 5 }] },
  })
  expect(merged2['100'].positions).toHaveLength(1)
  expect(merged2['100'].positions[0].position_id).toBe(7001)
  expect(merged2['100'].positions[0].current_price).toBe(1.10521) // untouched
})

test('the ApiState fold also patches master-position rows', () => {
  const state: ApiState = {
    accounts: snapshot,
    master_positions: [
      { position_id: 7001, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
        volume: 200000, price: 1.105, label: '', pnl_quote: 4.2,
        current_price: 1.10521, copies: [] },
      { position_id: 8888, symbol_id: 1, symbol: 'EURUSD', side: 'SELL',
        volume: 100000, price: 1.2, label: '', copies: [] },
    ],
    pending_orders: [],
    drift: [],
  }
  const merged = mergeTicksIntoApiState(state, {
    '100': { positions: [{ position_id: 7001, current_price: 1.10621, pnl_quote: 9.9 }] },
  })
  expect(merged.master_positions[0].current_price).toBe(1.10621)
  expect(merged.master_positions[0].pnl_quote).toBe(9.9)
  expect(merged.master_positions[1].current_price).toBeUndefined()
  expect(merged.accounts['100'].positions[0].current_price).toBe(1.10621)
})

test('an identical tick returns the same references — no re-render', () => {
  const same = mergeTicksIntoSnapshot(snapshot, {
    '100': {
      equity: 1004.2,
      open_pnl: 4.2,
      positions: [{ position_id: 7001, current_price: 1.10521, pnl_quote: 4.2 }],
    },
  })
  expect(same).toBe(snapshot)

  const state: ApiState = {
    accounts: snapshot, master_positions: [], pending_orders: [], drift: [],
  }
  expect(mergeTicksIntoApiState(state, {
    '100': {
      equity: 1004.2,
      open_pnl: 4.2,
      positions: [{ position_id: 7001, current_price: 1.10521, pnl_quote: 4.2 }],
    },
  })).toBe(state)
})

test('null marks and null equity are honest, never stale-frozen', () => {
  const merged = mergeTicksIntoSnapshot(snapshot, {
    '100': {
      equity: null, // balance unknown on the copier: must NOT keep 1004.2
      positions: [{ position_id: 7001, current_price: null, pnl_quote: null }],
    },
  })
  expect(merged['100'].equity).toBeNull()
  expect(merged['100'].positions[0].current_price).toBeNull()
  expect(merged['100'].positions[0].pnl_quote).toBeNull()
})
