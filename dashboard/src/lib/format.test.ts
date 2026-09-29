// src/lib/format.test.ts
import { afterEach, describe, expect, test, vi } from 'vitest'
import { formatWhen, money } from './format'

afterEach(() => { vi.useRealTimers() })

describe('money', () => {
  test('two decimals with separators; no unit keeps the old output', () => {
    expect(money(1234.5)).toBe('1,234.50')
    expect(money(0)).toBe('0.00')
    expect(money(-12.3)).toBe('-12.30')
  })

  test('a unit follows the amount after one space', () => {
    expect(money(500, 'USDT')).toBe('500.00 USDT')
    expect(money(5120.5, 'USD')).toBe('5,120.50 USD')
    expect(money(-12.3, 'USD')).toBe('-12.30 USD')
  })

  test('a form field string is read as a number', () => {
    expect(money('500', 'USDT')).toBe('500.00 USDT')
    expect(money('1000.5')).toBe('1,000.50')
  })

  test('an unknown amount is a bare dash, never a dash with a unit', () => {
    expect(money(null, 'USDT')).toBe('—')
    expect(money(undefined, 'USD')).toBe('—')
    expect(money('', 'USDT')).toBe('—')
    expect(money('abc', 'USDT')).toBe('—')
    expect(money(Number.NaN)).toBe('—')
  })
})

describe('formatWhen', () => {
  test('a date in the current year leaves the year out', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 29, 12, 0, 0))
    const out = formatWhen(new Date(2026, 2, 4, 10, 5, 6).getTime())
    expect(out).toContain('04')
    expect(out).toContain('10:05:06')
    expect(out).not.toContain('2026')
  })

  test('a date in another year shows the year', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 29, 12, 0, 0))
    const out = formatWhen(new Date(2025, 2, 4, 10, 5, 6).getTime())
    expect(out).toContain('2025')
    expect(out).toContain('10:05:06')
    expect(formatWhen(new Date(2025, 11, 31, 9, 0, 0).toISOString())).toContain('2025')
  })

  test('missing and unparseable values are unchanged', () => {
    expect(formatWhen(null)).toBe('—')
    expect(formatWhen(undefined)).toBe('—')
    expect(formatWhen('not a date')).toBe('not a date')
  })
})
