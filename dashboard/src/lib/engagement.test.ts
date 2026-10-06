import { expect, test } from 'vitest'
import {
  BONUS_SOURCE_LABELS, TEXTAREA, TICKET_STATUS_LABELS, TOPIC_EMAIL_LABELS, TOPICS, safeLink, ticketsQuery,
} from './engagement'

test('safeLink keeps our own paths and drops anything else', () => {
  expect(safeLink('/org/1/invest/deposit')).toBe('/org/1/invest/deposit')
  expect(safeLink(null)).toBeNull()
  expect(safeLink('https://evil.example')).toBeNull()
  expect(safeLink('//evil.example/x')).toBeNull()
})

test('ticketsQuery adds only the filters that are set', () => {
  expect(ticketsQuery('investor/tickets', 'all', '')).toBe('investor/tickets')
  expect(ticketsQuery('tickets', 'closed', '  wire 50% ')).toBe('tickets?status=closed&q=wire+50%25')
  expect(ticketsQuery('tickets', 'all', 'usdt')).toBe('tickets?q=usdt')
})

test('the shared textarea class stays on palette tokens', () => {
  expect(TEXTAREA).toBe('w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink')
})

test('every topic, status and source has its words', () => {
  expect(TOPICS).toEqual(['money', 'identity', 'support', 'bonus'])
  expect(Object.keys(TOPIC_EMAIL_LABELS)).toEqual(TOPICS)
  expect(TICKET_STATUS_LABELS).toEqual({ new: 'New', open: 'Open', closed: 'Closed' })
  expect(Object.keys(BONUS_SOURCE_LABELS)).toEqual(['signup', 'kyc', 'deposit', 'manual'])
})
