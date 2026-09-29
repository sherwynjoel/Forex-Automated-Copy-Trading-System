import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Requests from './Requests'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'
import {
  depositFixture, destinationFixture, transferFixture, withdrawalFixture,
} from '../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

class MockWebSocket {
  static instance: MockWebSocket | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor() { MockWebSocket.instance = this }
  close() { /* no-op */ }
  emit(json: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(json) }))
  }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const who = { user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor', currency: 'USD' }

const deposit = depositFixture({
  ...who, id: 11, method_id: 3, method_kind: 'bank', method_label: 'ICICI Bank',
  amount: 5000, fee: 50, credited_amount: null, reference: 'UTR123', receipt_file_id: 77,
  target: 'wallet', target_account_id: null, note: null, status: 'pending',
  decided_by: null, decided_at: null, decision_note: null, created_at: '2026-09-23T10:00:00Z',
})
const settled = depositFixture({
  ...deposit, id: 12, reference: 'UTR122', status: 'confirmed', credited_amount: 4950,
  decided_by: 1, decided_at: '2026-09-22T11:00:00Z', decision_note: 'seen on statement',
  created_at: '2026-09-22T10:00:00Z',
})
const approvedWd = withdrawalFixture({
  ...who, id: 21, destination_id: 41, destination_kind: 'bank', destination_summary: 'ICICI ••4543',
  amount: 1000, fee: 10, net_amount: 990, status: 'approved',
  decided_by: 1, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:30:00Z',
})
const requestedWd = withdrawalFixture({
  ...approvedWd, id: 22, amount: 200, fee: 2, net_amount: 198, status: 'requested',
  decided_by: null, decided_at: null, created_at: '2026-09-23T12:00:00Z',
})
const transfer = transferFixture({
  ...who, id: 31, source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
  amount: 250, status: 'requested', equity_at_request: null, equity_verified: true,
  decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null,
  note: null, created_at: '2026-09-23T13:00:00Z',
})
const destination = destinationFixture({
  ...who, id: 41, kind: 'bank', nickname: 'Salary account',
  details: { bank_name: 'ICICI', holder: 'Ada Investor', account_number: '000123454543', code: 'ICIC0000001' },
  proof_file_id: 78, status: 'pending', decided_by: null, decided_at: null, decision_note: null,
  created_at: '2026-09-23T09:00:00Z', summary: 'ICICI ••4543',
})

type Row = { id: number; status: string }

/** Every list is served from mutable copies: a decision POST flips the
 *  row's status, so the refetch that follows shows the queue drained. */
function mockRoutes() {
  const rows: Record<string, Row[]> = {
    deposits: [{ ...deposit }, { ...settled }],
    withdrawals: [{ ...approvedWd }, { ...requestedWd }],
    transfers: [{ ...transfer }],
    'payout-destinations': [{ ...destination }],
  }
  const summary = () => {
    const deposits = rows.deposits.filter((r) => r.status === 'pending').length
    const withdrawals = rows.withdrawals.filter((r) => r.status === 'requested' || r.status === 'approved').length
    const transfers = rows.transfers.filter((r) => r.status === 'requested' || r.status === 'approved').length
    const payout_destinations = rows['payout-destinations'].filter((r) => r.status === 'pending').length
    return { deposits, withdrawals, transfers, payout_destinations,
             total: deposits + withdrawals + transfers + payout_destinations }
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.endsWith('/requests/summary')) return jsonResponse(summary())
    for (const key of Object.keys(rows)) {
      if (url.endsWith(`/${key}`) && method === 'GET') return jsonResponse(rows[key])
    }
    const m = url.match(/\/(deposits|withdrawals|transfers|payout-destinations)\/(\d+)\/(decision|paid)$/)
    if (m && method === 'POST') {
      const body = JSON.parse(init!.body as string) as { status?: string }
      const row = rows[m[1]].find((r) => r.id === Number(m[2]))!
      row.status = m[3] === 'paid' ? 'paid' : body.status!
      return jsonResponse(row)
    }
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

function renderPage(path = '/org/1/requests') {
  return render(<MemoryRouter initialEntries={[path]}><Requests /></MemoryRouter>)
}

beforeEach(() => {
  MockWebSocket.instance = null
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('shows the heading and a loading state, then the tabs with their open counts', async () => {
  mockRoutes()
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Requests' })).toBeInTheDocument()
  expect(screen.getByRole('status', { name: 'Loading requests' })).toBeInTheDocument()
  expect(await screen.findByText('UTR123')).toBeInTheDocument()
  await waitFor(() => expect(document.title).toBe('Requests · MirrorFleet'))
  expect(screen.queryByRole('status', { name: 'Loading requests' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Deposits (1)' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Withdrawals (2)' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Transfers (1)' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Payout accounts (1)' })).toBeInTheDocument()
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'requests-tab-deposits')
  // The Open view leaves out the settled deposit.
  expect(screen.getByLabelText('Show')).toHaveValue('open')
  expect(screen.queryByText('UTR122')).not.toBeInTheDocument()
})

test('All shows settled rows too', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.selectOptions(screen.getByLabelText('Show'), 'all')
  expect(await screen.findByText('UTR122')).toBeInTheDocument()
  expect(screen.getByText('Confirmed')).toBeInTheDocument()
  expect(screen.getByText('seen on statement')).toBeInTheDocument()
})

test('opens on the tab named in the query string', async () => {
  mockRoutes()
  renderPage('/org/1/requests?tab=withdrawals')
  expect(await screen.findByRole('tab', { name: 'Withdrawals (2)' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('button', { name: 'Mark withdrawal 21 paid' })).toBeInTheDocument()
})

test('confirms a deposit with an edited credited amount, then the open queue is empty', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  const credited = screen.getByLabelText('Credited amount')
  expect(credited).toHaveValue('4950.00')
  await userEvent.clear(credited)
  await userEvent.type(credited, '4900.00')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/deposits/11/decision'))
    .toEqual({ status: 'confirmed', credited_amount: '4900.00', note: '' }))
  expect(await screen.findByText('Deposit confirmed')).toBeInTheDocument()
  expect(await screen.findByText('No open deposits')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Deposits (0)' })).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a malformed credited amount blocks Confirm; a rejection needs a note', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  const confirm = screen.getByRole('button', { name: 'Confirm' })
  await userEvent.clear(screen.getByLabelText('Credited amount'))
  expect(confirm).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Credited amount'), '4950')
  expect(confirm).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

  await userEvent.click(screen.getByRole('button', { name: 'Reject deposit 11' }))
  expect(screen.queryByLabelText('Credited amount')).not.toBeInTheDocument()
  const reject = screen.getByRole('button', { name: 'Reject' })
  expect(reject).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Note'), 'no such transaction')
  expect(reject).toBeEnabled()
})

test('approves a withdrawal and marks another paid with a transaction id', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('tab', { name: 'Withdrawals (2)' }))
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'requests-tab-withdrawals')
  await userEvent.click(await screen.findByRole('button', { name: 'Approve withdrawal 22' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/withdrawals/22/decision'))
    .toEqual({ status: 'approved', note: '' }))
  expect(await screen.findByText('Withdrawal approved')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Mark withdrawal 21 paid' }))
  const paid = screen.getByRole('button', { name: 'Mark paid' })
  expect(paid).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'chain-tx-1')
  await userEvent.click(paid)
  await waitFor(() => expect(bodyOf(fetchMock, '/withdrawals/21/paid')).toEqual({ txid: 'chain-tx-1' }))
  expect(await screen.findByText('Withdrawal marked paid')).toBeInTheDocument()
  // Paid and approved rows have left the open queue: only the approved one remains.
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Mark withdrawal 21 paid' })).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Mark withdrawal 22 paid' })).toBeInTheDocument()
})

test('marks a transfer done', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/requests?tab=transfers')
  expect(await screen.findByText('My wallet → Trading account 1001')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Mark transfer 31 done' }))
  await userEvent.click(screen.getByRole('button', { name: 'Mark done' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/transfers/31/decision')).toEqual({ status: 'done', note: '' }))
  expect(await screen.findByText('Transfer done')).toBeInTheDocument()
  expect(await screen.findByText('No open transfers')).toBeInTheDocument()
})

test('approves a payout account', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/requests?tab=payout_destinations')
  expect(await screen.findByText('Salary account')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Approve payout account 41' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payout-destinations/41/decision'))
    .toEqual({ status: 'approved', note: '' }))
  expect(await screen.findByText('Payout account approved')).toBeInTheDocument()
  expect(await screen.findByText('No open payout accounts')).toBeInTheDocument()
})

test('the details drawer shows the request, the investor, the receipt, the payout details and the audit trail', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Details of deposit 11' }))
  const drawer = await screen.findByRole('dialog', { name: 'Deposit #11' })
  expect(within(drawer).getByRole('img', { name: 'Receipt' })).toHaveAttribute('src', '/api/orgs/1/files/77')
  expect(within(drawer).getByRole('link', { name: 'Open file #77 in a new tab' })).toHaveAttribute('href', '/api/orgs/1/files/77')
  expect(within(drawer).getByText('inv@example.com')).toBeInTheDocument()
  expect(within(drawer).getByText('5,000.00 USD')).toBeInTheDocument()
  expect(within(drawer).getByText('Filed')).toBeInTheDocument()
  expect(within(drawer).queryByText('Decided')).not.toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  // A withdrawal's drawer shows the payout destination it pays to (fixture
  // destination 41 = approvedWd.destination_id), unmasked, not just the summary.
  await userEvent.click(screen.getByRole('tab', { name: 'Withdrawals (2)' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Details of withdrawal 21' }))
  const wd = await screen.findByRole('dialog', { name: 'Withdrawal #21' })
  expect(within(wd).getByText('ICICI ••4543 (bank)')).toBeInTheDocument()
  expect(within(wd).getByText('Salary account')).toBeInTheDocument()
  expect(within(wd).getByText('000123454543')).toBeInTheDocument()
  expect(within(wd).getByText('ICIC0000001')).toBeInTheDocument()
  expect(within(wd).getByText('Decided')).toBeInTheDocument()
  await userEvent.click(within(wd).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('tab', { name: 'Payout accounts (1)' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Details of payout account 41' }))
  const proof = await screen.findByRole('dialog', { name: 'Payout account #41' })
  expect(within(proof).getByRole('img', { name: 'Proof' })).toHaveAttribute('src', '/api/orgs/1/files/78')
  expect(within(proof).getByText('000123454543')).toBeInTheDocument()
  expect(within(proof).getByText('ICIC0000001')).toBeInTheDocument()
})

test('a control event refetches the queues', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  const summaryCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/requests/summary')).length
  const before = summaryCalls()
  act(() => {
    MockWebSocket.instance!.emit({ category: 'control', payload: { action: 'investor_deposit_noticed', user_id: 5 } })
  })
  await act(async () => { await vi.advanceTimersByTimeAsync(300) })
  await waitFor(() => expect(summaryCalls()).toBeGreaterThan(before))
})

test('a viewer sees the queues and the details but no decisions', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  expect(screen.queryByRole('button', { name: 'Confirm deposit 11' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Details of deposit 11' })).toBeInTheDocument()
})

test('a 409 on confirming a deposit is shown inside the dialog, not the page banner, and the dialog stays open', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.endsWith('/requests/summary')) {
      return jsonResponse({ deposits: 1, withdrawals: 0, transfers: 0, payout_destinations: 0, total: 1 })
    }
    if (url.endsWith('/deposits') && method === 'GET') return jsonResponse([deposit])
    if (url.endsWith('/withdrawals') && method === 'GET') return jsonResponse([])
    if (url.endsWith('/transfers') && method === 'GET') return jsonResponse([])
    if (url.endsWith('/payout-destinations') && method === 'GET') return jsonResponse([])
    if (url.endsWith('/deposits/11/decision') && method === 'POST') {
      return jsonResponse({ detail: 'deposit is already confirmed' }, 409)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByText('deposit is already confirmed')).toBeInTheDocument()
  // Still open -- the row's own state, not closed and dumped on the page banner.
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  expect(screen.queryByText('Deposit confirmed')).not.toBeInTheDocument()
  expect(screen.getAllByText('deposit is already confirmed')).toHaveLength(1)
})
