import { describe, expect, test } from 'vitest'
import {
  ACCOUNT_CURRENCY, BADGE_TONE, WALLETS, approvedLabel, entryLabel, moneyOrDash, shortAddress,
  statusLabel, statusTone, walletLabel,
} from './investor'
import type { WalletEntry } from './types'

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

describe('portal vocabulary', () => {
  test('the four wallets, in display order, with their names', () => {
    expect(WALLETS).toEqual(['main', 'credit', 'pamm', 'social'])
    expect(walletLabel('main')).toBe('My wallet')
    expect(walletLabel('credit')).toBe('Credit wallet')
    expect(walletLabel('pamm')).toBe('PAMM wallet')
    expect(walletLabel('social')).toBe('Social wallet')
  })

  test('the phase-1 statuses: cancelled and removed are quiet, done is ok', () => {
    expect(statusLabel('cancelled')).toBe('Cancelled')
    expect(statusTone('cancelled')).toBe('quiet')
    expect(statusLabel('done')).toBe('Done')
    expect(statusTone('done')).toBe('ok')
    expect(statusLabel('removed')).toBe('Removed')
    expect(statusTone('removed')).toBe('quiet')
  })

  test('approved reads per request kind: a transfer is in progress, a payout account is simply approved', () => {
    expect(approvedLabel('withdrawal')).toBe('Approved, payment pending')
    expect(approvedLabel('transfer')).toBe('Approved, in progress')
    expect(approvedLabel('destination')).toBe('Approved')
    expect(statusLabel('approved', 'transfer')).toBe('Approved, in progress')
    expect(statusLabel('approved', 'withdrawal')).toBe('Approved, payment pending')
    expect(statusLabel('approved', 'destination')).toBe('Approved')
    expect(statusTone('approved', 'destination')).toBe('ok')
    expect(statusTone('approved', 'transfer')).toBe('warn')
    expect(statusLabel('requested', 'transfer')).toBe('Awaiting approval')
  })

  test('the four tones map onto the Badge tones', () => {
    expect(BADGE_TONE).toEqual({ ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral' })
  })

  test('ledger entries are named by kind and reference', () => {
    const entry = (over: Partial<WalletEntry>): WalletEntry => ({
      id: 1, wallet: 'main', amount: 250, kind: 'deposit', ref_table: 'deposits', ref_id: 12,
      note: null, created_at: '2026-09-29T10:05:06Z', currency: 'USD', ...over,
    })
    expect(entryLabel(entry({}))).toBe('Deposit #12')
    expect(entryLabel(entry({ kind: 'withdrawal', ref_table: 'withdrawals', ref_id: 4, amount: -250 }))).toBe('Withdrawal #4')
    expect(entryLabel(entry({ kind: 'transfer', ref_table: 'transfers', ref_id: 9 }))).toBe('Transfer #9')
    expect(entryLabel(entry({ kind: 'adjustment', ref_table: null, ref_id: null }))).toBe('Adjustment')
    expect(entryLabel(entry({ kind: 'bonus', ref_table: null, ref_id: null }))).toBe('Bonus')
    expect(entryLabel(entry({ kind: 'commission', ref_table: null, ref_id: null }))).toBe('Commission')
    expect(entryLabel(entry({ kind: 'fee', ref_table: null, ref_id: null }))).toBe('Fee')
  })
})
