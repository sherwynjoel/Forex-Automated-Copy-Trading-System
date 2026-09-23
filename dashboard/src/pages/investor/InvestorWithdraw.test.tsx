import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true, total_deposited: 5000, total_withdrawn: 0,
  net_deposits: 5000, pending_withdrawn: 0, equity: 5120.5, profit: 120.5, available: 5120.5,
}
const request = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                  status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                  decided_by: 2, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { refuse?: string; unlinked?: boolean } = {}) {
  const rows: unknown[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.unlinked
        ? { ...summary, link_state: 'unlinked', account: null, available: null } : summary)
    }
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse({ detail: opts.refuse }, 400)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows what is available and files a request', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('5,120.50')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Amount'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string))
    .toEqual({ amount: '1000', destination: 'TDest' })
  await waitFor(() => expect(screen.getByText('Approved, payment pending')).toBeInTheDocument())
})

test("the server's refusal is shown as written", async () => {
  mockRoutes({ refuse: 'amount exceeds what is available to withdraw (1120.50)' })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,120.50')
  await userEvent.type(screen.getByLabelText('Amount'), '9999')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText(/available to withdraw \(1120\.50\)/)).toBeInTheDocument()
})

test('unlinked investors cannot request yet', async () => {
  mockRoutes({ unlinked: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})
