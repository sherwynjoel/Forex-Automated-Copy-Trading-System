import { expect, test } from 'vitest'
import { cumulativeSeries, drawdownSeries, dailyPnl } from './perf'

const curve = [
  { timestamp: 1000, balance: 10000 },
  { timestamp: 2000, balance: 10250 },
  { timestamp: 3000, balance: 10200 },
]

test('cumulativeSeries measures P&L relative to the window start', () => {
  expect(cumulativeSeries(curve)).toEqual([
    { t: 1000, v: 0 },
    { t: 2000, v: 250 },
    { t: 3000, v: 200 },
  ])
})

test('cumulativeSeries of an empty curve is empty', () => {
  expect(cumulativeSeries([])).toEqual([])
})

test('drawdownSeries tracks distance below the running peak', () => {
  expect(drawdownSeries(curve)).toEqual([
    { t: 1000, v: 0 },
    { t: 2000, v: 0 },
    { t: 3000, v: -50 },
  ])
})

test('dailyPnl buckets closed trades into one bar per local calendar day', () => {
  const dayA = new Date(2026, 7, 18, 9).getTime()
  const dayB = new Date(2026, 7, 20, 15).getTime()
  const bars = dailyPnl([
    // First point doubles as the baseline: its own trade P&L is unknowable
    // from balance-after alone, mirroring the strip's window logic.
    { timestamp: dayA, balance: 10000 },
    { timestamp: dayA + 3600000, balance: 10100 },
    { timestamp: dayB, balance: 10150 },
    { timestamp: dayB + 3600000, balance: 10080 },
  ])
  expect(bars).toEqual([
    { t: new Date(2026, 7, 18).getTime(), v: 100 },
    { t: new Date(2026, 7, 20).getTime(), v: -20 },
  ])
})

test('dailyPnl of a single point yields one flat bar', () => {
  const dayA = new Date(2026, 7, 18, 9).getTime()
  expect(dailyPnl([{ timestamp: dayA, balance: 10000 }])).toEqual([
    { t: new Date(2026, 7, 18).getTime(), v: 0 },
  ])
})
