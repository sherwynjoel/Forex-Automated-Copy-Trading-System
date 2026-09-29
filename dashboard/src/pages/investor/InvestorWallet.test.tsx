import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWallet from './InvestorWallet'
import { mockUseOrg } from '../../test/orgMock'
import { entryFixture, summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD',
  wallets: {
    main: { balance: 3000, on_hold: 100, available: 2900 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 1000, on_hold: 0, available: 1000 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
}
const entry = entryFixture({
  id: 1, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: { fail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.includes('/investor/wallet-entries')) return jsonResponse({ entries: [entry], has_more: true, next_before: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
// Unmount before resetting the hide-balances store: Vitest runs afterEach
// hooks in reverse order, so RTL's auto-cleanup (registered before this
// file's hooks run) would otherwise fire AFTER setHidden(false) here, while
// the tree is still mounted -- the broadcast then updates Money/ShareCell
// outside act(). Explicit cleanup() first makes the unmount happen before
// the store changes, no matter what order the hooks run in.
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setHidden(false) })

test('shows the four wallets with their share, quick actions and the last entries', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWallet /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Wallet' })).toBeInTheDocument()
  expect(document.title).toBe('Wallet · MirrorFleet')
  const table = await screen.findByRole('table', { name: 'Your wallets' })
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows).toHaveLength(4)
  expect(rows[0]).toHaveTextContent('My wallet')
  expect(rows[0]).toHaveTextContent('3,000.00 USD')
  expect(rows[0]).toHaveTextContent('75%')
  expect(rows[0]).toHaveTextContent('2,900.00 USD')
  expect(rows[2]).toHaveTextContent('PAMM wallet')
  expect(rows[2]).toHaveTextContent('25%')
  expect(screen.getByRole('link', { name: 'Deposit' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'Withdraw' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByRole('link', { name: 'Transfer' })).toHaveAttribute('href', '/org/1/invest/transfer')
  expect(screen.getByRole('link', { name: 'Transactions' })).toHaveAttribute('href', '/org/1/invest/transactions')
  expect(screen.getByText('Deposit #1')).toBeInTheDocument()
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/org/1/invest/transactions')
  const entriesCall = fetchMock.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/investor/wallet-entries'))
  expect(entriesCall).toMatch(/limit=20$/)
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorWallet /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Nothing yet')).toBeInTheDocument()
})

test('hiding balances masks every wallet figure and drops the share bars, not just the numbers', async () => {
  mockRoutes()
  setHidden(true)
  render(<MemoryRouter><InvestorWallet /></MemoryRouter>)
  const table = await screen.findByRole('table', { name: 'Your wallets' })
  expect(screen.queryByText('3,000.00 USD')).not.toBeInTheDocument()
  expect(screen.queryByText('2,900.00 USD')).not.toBeInTheDocument()
  expect(within(table).queryByText('75%')).not.toBeInTheDocument()
  expect(within(table).queryByText('25%')).not.toBeInTheDocument()
  expect(screen.queryByText(/5,000\.00/)).not.toBeInTheDocument()
})
