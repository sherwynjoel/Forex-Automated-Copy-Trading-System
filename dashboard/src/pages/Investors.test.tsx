import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Investors from './Investors'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

class MockWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close() { /* no-op */ }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const investor = { user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor',
                   account_id: 1001, nickname: 'Inv', equity: 5120.5, net_deposits: 5000,
                   profit: 120.5, pending_deposits: 1, pending_withdrawals: 1 }
const deposit = { id: 11, user_id: 5, account_id: null, amount: 5000, coin: 'USDT', txid: 'abc',
                  note: null, status: 'pending', decided_by: null, decided_at: null,
                  decision_note: null, created_at: '2026-09-23T10:00:00Z',
                  email: 'inv@example.com', display_name: 'Ada Investor' }
const withdrawal = { id: 21, user_id: 5, account_id: 1001, amount: 1000, destination: 'TDest',
                     status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                     decided_by: 1, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                     paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:30:00Z',
                     email: 'inv@example.com', display_name: 'Ada Investor' }

function mockRoutes(options: { wallets?: unknown[] } = {}) {
  const { wallets } = options
  let walletGetCalls = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.endsWith('/investors')) return jsonResponse([investor])
    if (url.endsWith('/investor-deposits')) return jsonResponse([deposit])
    if (url.endsWith('/investor-withdrawals')) return jsonResponse([withdrawal])
    if (url.endsWith('/investor-wallet') && method === 'GET') {
      if (wallets && wallets.length > 0) {
        const idx = Math.min(walletGetCalls, wallets.length - 1)
        walletGetCalls += 1
        return jsonResponse(wallets[idx])
      }
      return jsonResponse({ detail: 'none' }, 404)
    }
    if (url.endsWith('/investor-wallet')) return jsonResponse(JSON.parse(init!.body as string))
    if (url.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
    if (url.includes('/decision') || url.includes('/paid')) return jsonResponse({ ...deposit, status: 'confirmed' })
    if (url.includes('/investors/5/account')) return jsonResponse({ user_id: 5, account_id: null })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).includes(fragment) && (init as RequestInit)?.method === 'POST')
  return JSON.parse((call![1] as RequestInit).body as string)
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('lists investors with their figures', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  // "Ada Investor" legitimately appears both in the investors table and in
  // the (default-shown) pending deposit row, so a singular query throws.
  expect((await screen.findAllByText('Ada Investor')).length).toBeGreaterThan(0)
  expect(screen.getByText('5,120.50')).toBeInTheDocument()
  expect(screen.getByText('120.50')).toBeInTheDocument()
})

test('confirms a deposit, and a rejection needs a note', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect((await screen.findAllByText('Ada Investor')).length).toBeGreaterThan(0)
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor-deposits/11/decision'))
    .toEqual({ status: 'confirmed', note: '' }))

  await userEvent.click(screen.getByRole('button', { name: 'Reject deposit 11' }))
  const reject = screen.getByRole('button', { name: 'Reject' })
  expect(reject).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Note'), 'no such transaction')
  expect(reject).toBeEnabled()
})

test('marks an approved withdrawal paid with a transaction id', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect((await screen.findAllByText('Ada Investor')).length).toBeGreaterThan(0)
  await userEvent.click(screen.getByRole('tab', { name: /Withdrawals/ }))
  await userEvent.click(await screen.findByRole('button', { name: 'Mark withdrawal 21 paid' }))
  const paid = screen.getByRole('button', { name: 'Mark paid' })
  expect(paid).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'chain-tx-1')
  await userEvent.click(paid)
  await waitFor(() => expect(bodyOf(fetchMock, '/investor-withdrawals/21/paid'))
    .toEqual({ txid: 'chain-tx-1' }))
})

test('saves the wallet card', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect((await screen.findAllByText('Ada Investor')).length).toBeGreaterThan(0)
  await userEvent.type(screen.getByLabelText('Coin'), 'USDT')
  await userEvent.type(screen.getByLabelText('Network'), 'TRC20')
  await userEvent.type(screen.getByLabelText('Address'), 'TAddr123')
  await userEvent.click(screen.getByRole('button', { name: 'Save wallet' }))
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u).endsWith('/investor-wallet') && (init as RequestInit)?.method === 'PUT')
    expect(JSON.parse((call![1] as RequestInit).body as string))
      .toEqual({ coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: '' })
  })
})

test('the wallet card follows the server when the form is untouched', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes({ wallets: [
    { coin: 'USDT', network: 'TRC20', address: 'TFirst', memo: null },
    { coin: 'USDT', network: 'TRC20', address: 'TSecond', memo: null },
  ] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByDisplayValue('TFirst')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(10000)
  expect(await screen.findByDisplayValue('TSecond')).toBeInTheDocument()
})
