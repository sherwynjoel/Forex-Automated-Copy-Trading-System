import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorTransfer, { pairAllowed, transferOptions } from './InvestorTransfer'
import { mockUseOrg } from '../../test/orgMock'
import { accountSummaryFixture, summaryFixture, transferFixture } from '../../test/portalFixtures'
import type { InvestorSummary, PortalTransfer } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const inv = accountSummaryFixture({
  account_id: 1001, nickname: 'Inv', mt5_login: null, mt5_server: null, equity: 2500, account_available: 2500,
})
const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', accounts: [inv],
  wallets: {
    main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 300, on_hold: 0, available: 300 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
  equity_source: 'live', equity: 2500,
}
const unlinked: InvestorSummary = { ...linked, accounts: [], equity_source: 'unknown', equity: null }
const requested: PortalTransfer = transferFixture({
  id: 9, user_id: 1, source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
  amount: 1000, status: 'requested', equity_at_request: null, equity_verified: false,
  decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null, note: null,
  created_at: '2026-09-24T10:00:00Z', currency: 'USD',
})
const done: PortalTransfer = transferFixture({
  ...requested, id: 10, source: { kind: 'wallet', wallet: 'pamm' }, target: { kind: 'wallet', wallet: 'main' },
  amount: 300, status: 'done', done_at: '2026-09-24T10:00:01Z',
})

function mockRoutes(opts: {
  summary?: InvestorSummary; rows?: PortalTransfer[]; created?: PortalTransfer
  refuse?: { status: number; body: unknown }; fail?: boolean
} = {}) {
  const rows: PortalTransfer[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) return jsonResponse(opts.summary ?? linked)
    if (url.endsWith('/investor/transfers') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse(opts.refuse.body, opts.refuse.status)
      const created = opts.created ?? requested
      rows.unshift(created)
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/transfers')) return jsonResponse(rows)
    if (/\/investor\/transfers\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      rows[0] = { ...rows[0], status: 'cancelled' }
      return jsonResponse(rows[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('transferOptions lists the three movable wallets plus the linked account; pairAllowed follows the phase 1 rules', () => {
  expect(transferOptions(linked).map((o) => o.value)).toEqual(['wallet:main', 'wallet:pamm', 'wallet:social', 'account:1001'])
  expect(transferOptions(unlinked).map((o) => o.value)).toEqual(['wallet:main', 'wallet:pamm', 'wallet:social'])
  expect(pairAllowed('wallet:main', 'account:1001')).toBe(true)
  expect(pairAllowed('account:1001', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:pamm', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:social', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:main', 'wallet:pamm')).toBe(false)
  expect(pairAllowed('wallet:pamm', 'account:1001')).toBe(false)
  expect(pairAllowed('wallet:main', 'wallet:main')).toBe(false)
})

test('moves wallet money to the trading account after the MPIN, with the exact payload', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Transfer' })).toBeInTheDocument()
  expect(document.title).toBe('Transfer · MirrorFleet')
  const from = await screen.findByLabelText('From')
  expect(from).toHaveValue('wallet:main')
  expect(screen.getByLabelText('To')).toHaveValue('account:1001')
  expect(screen.getByText('5,020.50 USD')).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 1,000.00 USD from My wallet to Trading account?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/transfers$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
    amount: '1000', mpin: '123456',
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText('Request sent. An admin moves the money and marks it done.')).toBeInTheDocument()
  expect(screen.getByText('Awaiting approval')).toBeInTheDocument()
  expect(screen.getByText('My wallet → Trading account')).toBeInTheDocument()
})

test('PAMM to My wallet completes at once and says so', async () => {
  const fetchMock = mockRoutes({ created: done })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.selectOptions(await screen.findByLabelText('From'), 'wallet:pamm')
  const to = screen.getByLabelText('To')
  expect(within(to).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['wallet:main'])
  expect(screen.getByText('300.00 USD')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('300.00')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 300.00 USD from PAMM wallet to My wallet?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    source: { kind: 'wallet', wallet: 'pamm' }, target: { kind: 'wallet', wallet: 'main' },
    amount: '300.00', mpin: '123456',
  })
  expect(await screen.findByText('Transfer done. Your wallets are updated.')).toBeInTheDocument()
  expect(screen.getByText('Done')).toBeInTheDocument()
})

test('without a linked account My wallet has nowhere to go, but PAMM still moves to My wallet', async () => {
  mockRoutes({ summary: unlinked })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const from = await screen.findByLabelText('From')
  expect(within(screen.getByLabelText('To')).queryAllByRole('option')).toHaveLength(0)
  expect(screen.getByText(/Link a trading account to move wallet money into it/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Request transfer' })).toBeDisabled()
  await userEvent.selectOptions(from, 'wallet:pamm')
  expect(screen.getByLabelText('To')).toHaveValue('wallet:main')
  expect(screen.getByRole('button', { name: 'Request transfer' })).toBeEnabled()
})

test('a wrong MPIN stays in the dialog with the tries left', async () => {
  const fetchMock = mockRoutes({ refuse: { status: 401, body: { detail: 'Invalid MPIN', attempts_left: 1 } } })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '10')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '111111')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  expect(await screen.findByText('Wrong MPIN, 1 try left')).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(1)
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '0')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('a requested transfer can be cancelled after a confirmation; a done one cannot', async () => {
  const fetchMock = mockRoutes({ rows: [requested, done] })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByText('Done')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel transfer 10' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel transfer 9' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel transfer #9?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/transfers\/9\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
})

test('a transfer that the admin acknowledged reads "Approved, in progress"', async () => {
  mockRoutes({ rows: [{ ...requested, status: 'approved', decided_by: 2, decided_at: '2026-09-24T11:00:00Z' }] })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByText('Approved, in progress')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel transfer 9' })).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No transfers yet')).toBeInTheDocument()
})

test('several accounts each become a choice with their own available figure, named in the review', async () => {
  const swing = accountSummaryFixture({ account_id: 1002, nickname: 'Swing', mt5_login: 6002, account_available: 300 })
  const fetchMock = mockRoutes({ summary: { ...linked, accounts: [inv, swing] } })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const to = await screen.findByLabelText('To')
  expect(within(to).getAllByRole('option').map((o) => o.textContent))
    .toEqual(['Trading account Inv', 'Trading account MT5 6002'])
  await userEvent.selectOptions(screen.getByLabelText('From'), 'account:1002')
  expect(screen.getByText('300.00 USD')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Amount in USD'), '100')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 100.00 USD from Trading account MT5 6002 to My wallet?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    source: { kind: 'account', account_id: 1002 }, target: { kind: 'wallet', wallet: 'main' },
    amount: '100', mpin: '123456',
  })
})
