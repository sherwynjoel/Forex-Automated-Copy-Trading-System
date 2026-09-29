import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Investors from './Investors'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'
import { entryFixture, investorRowFixture, methodFixture } from '../test/portalFixtures'

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

const investor = investorRowFixture({
  user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor',
  joined_at: '2026-09-01T00:00:00Z', account_id: 1001, nickname: 'Inv',
  equity: 6000, equity_source: 'live',
  balances: { main: 5120.5, credit: 0, pamm: 0, social: 0 }, on_hold: 100, available: 5020.5,
  pending: { deposits: 2, withdrawals: 1, transfers: 0, payout_destinations: 0 },
})
const usdt = methodFixture({
  id: 3, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
  details: { coin: 'USDT', network: 'TRC20', address: 'TXYZ1234567890abcdef' },
  min_amount: 10, fee_pct: 0, instructions: null, sort_order: 0,
})
const entry = entryFixture({
  id: 900, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 11,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})
const older = entryFixture({
  id: 899, wallet: 'main', amount: -25, kind: 'adjustment', ref_table: null, ref_id: null,
  note: 'Correction', created_at: '2026-09-19T10:00:00Z', currency: 'USD',
})

function mockRoutes(options: {
  settings?: unknown[]
  refuseAdjustment?: { status: number; body: unknown }
  refuseDelete?: { status: number; body: unknown }
} = {}) {
  const queue = options.settings ? [...options.settings] : [{ withdrawal_min: 0, withdrawal_fee_pct: 0 }]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const path = url.split('?')[0]
    if (path.endsWith('/investors')) return jsonResponse([investor])
    if (path.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
    if (path.endsWith('/payment-methods') && method === 'GET') return jsonResponse([usdt])
    if (path.endsWith('/payment-methods') && method === 'POST') {
      return jsonResponse({ ...usdt, id: 4, ...JSON.parse(init!.body as string) }, 201)
    }
    if (path.includes('/payment-methods/') && method === 'PATCH') {
      return jsonResponse({ ...usdt, ...JSON.parse(init!.body as string) })
    }
    if (path.includes('/payment-methods/') && method === 'DELETE') {
      if (options.refuseDelete) return jsonResponse(options.refuseDelete.body, options.refuseDelete.status)
      return new Response(null, { status: 204 })
    }
    if (path.endsWith('/portal-settings') && method === 'GET') {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
    if (path.endsWith('/portal-settings')) return jsonResponse(JSON.parse(init!.body as string))
    if (path.endsWith('/investors/5/wallet-entries')) {
      if (url.includes('before=')) return jsonResponse({ entries: [older], has_more: false, next_before: null })
      return jsonResponse({ entries: [entry], has_more: true, next_before: 900 })
    }
    if (path.endsWith('/investors/5/adjustments')) {
      if (options.refuseAdjustment) return jsonResponse(options.refuseAdjustment.body, options.refuseAdjustment.status)
      return jsonResponse({ ...older, id: 901 }, 201)
    }
    if (path.endsWith('/investors/5/account')) return jsonResponse({ user_id: 5, account_id: null })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).includes(fragment) && (init as RequestInit)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

function rowFor(text: string): HTMLElement {
  const row = screen.getAllByRole('row').find((r) => r.textContent?.includes(text))
  if (!row) throw new Error(`no row contains ${text}`)
  return row
}

async function chooseFromMenu(rowText: string, item: string) {
  await userEvent.click(within(rowFor(rowText)).getByRole('button', { name: 'Actions for inv@example.com' }))
  const menu = await screen.findByRole('menu')
  await userEvent.click(within(menu).getByRole('menuitem', { name: item }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('shows the heading and a loading state, then the investors with their wallet figures', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Investors' })).toBeInTheDocument()
  expect(screen.getByRole('status', { name: 'Loading investors' })).toBeInTheDocument()
  expect(screen.queryByText(/No investors yet/)).not.toBeInTheDocument()
  expect(await screen.findByText('Ada Investor')).toBeInTheDocument()
  await waitFor(() => expect(document.title).toBe('Investors · MirrorFleet'))
  expect(screen.queryByRole('status', { name: 'Loading investors' })).not.toBeInTheDocument()
  expect(screen.getByText('5,120.50')).toBeInTheDocument()
  expect(screen.getByText('on hold 100.00 · available 5,020.50')).toBeInTheDocument()
  expect(screen.getByText('6,000.00')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '2 deposits' })).toHaveAttribute('href', '/org/1/requests?tab=deposits')
  expect(screen.getByRole('link', { name: '1 withdrawal' })).toHaveAttribute('href', '/org/1/requests?tab=withdrawals')
  expect(screen.queryByRole('link', { name: /transfer/ })).not.toBeInTheDocument()
})

test('links an account from the row select', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.selectOptions(screen.getByLabelText('Account for inv@example.com'), '1002')
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/account', 'PUT')).toEqual({ account_id: 1002 }))
  expect(await screen.findByText('Account linked')).toBeInTheDocument()
})

test('View ledger opens the drawer, loads more and filters by wallet', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'View ledger')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's ledger" })
  expect(await within(drawer).findByText('Deposit #11')).toBeInTheDocument()
  expect(within(drawer).getByText('+5,000.00')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50'))).toBe(true)

  await userEvent.click(within(drawer).getByRole('button', { name: 'Load more' }))
  expect(await within(drawer).findByText('Correction')).toBeInTheDocument()
  expect(within(drawer).getByText('-25.00')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50&before=900'))).toBe(true)
  // The last page is in: nothing more to load.
  expect(within(drawer).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()

  await userEvent.selectOptions(within(drawer).getByLabelText('Wallet'), 'pamm')
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50&wallet=pamm'))).toBe(true))
})

test('Adjust balance posts a signed entry with the admin MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Adjust balance')
  const dialog = await screen.findByRole('dialog', { name: "Adjust Ada Investor's wallet" })
  await userEvent.type(within(dialog).getByLabelText('Amount'), '-25.00')
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Correction')
  const post = within(dialog).getByRole('button', { name: 'Post adjustment' })
  expect(post).toBeDisabled()
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  expect(post).toBeEnabled()
  await userEvent.click(post)
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/adjustments', 'POST'))
    .toEqual({ wallet: 'main', amount: '-25.00', note: 'Correction', mpin: '123456' }))
  expect(await screen.findByText('Adjustment of -25.00 posted to My wallet')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a stacked sign or a zero amount is refused before it reaches the server', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Adjust balance')
  const dialog = await screen.findByRole('dialog', { name: "Adjust Ada Investor's wallet" })
  const amount = within(dialog).getByLabelText('Amount')
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Correction')

  for (const bad of ['+-5', '--5', '- 5', '0']) {
    await userEvent.clear(amount)
    await userEvent.type(amount, bad)
    await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
    await userEvent.keyboard('123456')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Post adjustment' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('dialog', { name: "Adjust Ada Investor's wallet" })).toBeInTheDocument()
  }
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/investors/5/adjustments'))).toBe(false)
})

test('a wrong MPIN stays in the dialog with the tries left, and never bounces to /login', async () => {
  const fetchMock = mockRoutes({ refuseAdjustment: { status: 401, body: { detail: 'Invalid MPIN', attempts_left: 2 } } })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Adjust balance')
  const dialog = await screen.findByRole('dialog', { name: "Adjust Ada Investor's wallet" })
  await userEvent.type(within(dialog).getByLabelText('Amount'), '-25.00')
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Correction')
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('111111')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Post adjustment' }))
  expect(await screen.findByText('Wrong MPIN, 2 tries left')).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: "Adjust Ada Investor's wallet" })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/investors/5/adjustments') && (init as RequestInit)?.method === 'POST')).toBe(true)
  expect(screen.queryByText('Unauthorized')).not.toBeInTheDocument()
})

test('Payment methods tab lists the methods and disables one', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'investors-tab-methods')
  expect(screen.getByText('USDT on TRC20')).toBeInTheDocument()
  expect(screen.getByText('USDT · TRC20 · T…ef')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Disable USDT on TRC20' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payment-methods/3', 'PATCH')).toEqual({ enabled: false }))
  expect(await screen.findByText('Method disabled')).toBeInTheDocument()
})

test('adds a bank method from the drawer', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  await userEvent.selectOptions(within(drawer).getByLabelText('Kind'), 'bank')
  await userEvent.type(within(drawer).getByLabelText('Label'), 'ICICI Bank')
  await userEvent.type(within(drawer).getByLabelText('Bank name'), 'ICICI')
  await userEvent.type(within(drawer).getByLabelText('Account holder'), 'MirrorFleet Ltd')
  await userEvent.type(within(drawer).getByLabelText('Account number'), '000123456789')
  await userEvent.type(within(drawer).getByLabelText('SWIFT / IFSC code'), 'ICIC0000001')
  await userEvent.clear(within(drawer).getByLabelText('Minimum deposit'))
  await userEvent.type(within(drawer).getByLabelText('Minimum deposit'), '500')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payment-methods', 'POST')).toEqual({
    kind: 'bank', label: 'ICICI Bank', currency: 'USD',
    details: { bank_name: 'ICICI', holder: 'MirrorFleet Ltd', account_number: '000123456789', code: 'ICIC0000001' },
    min_amount: '500', fee_pct: '0', instructions: null, sort_order: 0,
  }))
  expect(await screen.findByText('Payment method added')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a missing required detail is refused before anything is sent', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  await userEvent.type(within(drawer).getByLabelText('Label'), 'USDT on BEP20')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('Coin is required')
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'POST')).toBe(false)
})

test('deleting a method asks first', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Delete USDT on TRC20' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete USDT on TRC20?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete method' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/payment-methods/3') && (init as RequestInit)?.method === 'DELETE')).toBe(true))
  expect(await screen.findByText('Payment method deleted')).toBeInTheDocument()
})

test('a method with a pending deposit refuses the delete, and the refusal is shown', async () => {
  mockRoutes({ refuseDelete: { status: 409, body: { detail: 'a pending deposit still uses this method' } } })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Delete USDT on TRC20' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete USDT on TRC20?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete method' }))
  expect(await screen.findByText('a pending deposit still uses this method')).toBeInTheDocument()
  expect(screen.getByText('USDT on TRC20')).toBeInTheDocument()
})

test('saves the withdrawal settings', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  const save = screen.getByRole('button', { name: 'Save withdrawal settings' })
  expect(save).toBeDisabled()
  await userEvent.clear(screen.getByLabelText('Minimum withdrawal'))
  await userEvent.type(screen.getByLabelText('Minimum withdrawal'), '25')
  await userEvent.clear(screen.getByLabelText('Withdrawal fee %'))
  await userEvent.type(screen.getByLabelText('Withdrawal fee %'), '1.5')
  expect(save).toBeEnabled()
  await userEvent.click(save)
  await waitFor(() => expect(bodyOf(fetchMock, '/portal-settings', 'PUT'))
    .toEqual({ withdrawal_min: '25', withdrawal_fee_pct: '1.5' }))
  expect(await screen.findByText('Withdrawal settings saved')).toBeInTheDocument()
})

test('the withdrawal settings follow the server while the form is untouched', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes({ settings: [
    { withdrawal_min: 0, withdrawal_fee_pct: 0 },
    { withdrawal_min: 50, withdrawal_fee_pct: 1 },
  ] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  fireEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('0')
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  await waitFor(() => expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('50'))
  expect(screen.getByLabelText('Withdrawal fee %')).toHaveValue('1')
})

test('a viewer sees the figures but no actions', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  expect(screen.queryByRole('button', { name: 'Actions for inv@example.com' })).not.toBeInTheDocument()
  expect(screen.getByText('Inv')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.queryByRole('button', { name: 'Add method' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save withdrawal settings' })).not.toBeInTheDocument()
})
