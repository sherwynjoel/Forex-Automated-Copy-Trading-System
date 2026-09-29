import { describe, expect, test } from 'vitest'
import { ACCOUNT_CURRENCY, moneyOrDash, shortAddress, statusLabel, statusTone } from './investor'

describe('investor helpers', () => {
  test('statuses read as plain words with a tone', () => {
    expect(statusLabel('pending')).toBe('Pending review')
    expect(statusLabel('confirmed')).toBe('Confirmed')
    expect(statusLabel('requested')).toBe('Awaiting approval')
    expect(statusLabel('approved')).toBe('Approved, payment pending')
    expect(statusLabel('paid')).toBe('Paid')
    expect(statusLabel('rejected')).toBe('Rejected')
    expect(statusTone('confirmed')).toBe('ok')
    expect(statusTone('paid')).toBe('ok')
    expect(statusTone('pending')).toBe('warn')
    expect(statusTone('requested')).toBe('warn')
    expect(statusTone('approved')).toBe('warn')
    expect(statusTone('rejected')).toBe('bad')
    expect(statusTone('whatever')).toBe('quiet')
  })

  test('money shows a dash when unknown', () => {
    expect(moneyOrDash(null)).toBe('—')
    expect(moneyOrDash(undefined)).toBe('—')
    expect(moneyOrDash(1234.5)).toBe('1,234.50')
  })

  test('money carries its unit, and the dash never does', () => {
    expect(moneyOrDash(1234.5, 'USD')).toBe('1,234.50 USD')
    expect(moneyOrDash(null, 'USD')).toBe('—')
    expect(ACCOUNT_CURRENCY).toBe('USD')
  })

  test('long addresses shorten to first character and last two; short ones stay whole', () => {
    expect(shortAddress('TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')).toBe('T…9f')
    expect(shortAddress('TDest')).toBe('TDest')
    expect(shortAddress('  TDest  ')).toBe('TDest')
  })
})
