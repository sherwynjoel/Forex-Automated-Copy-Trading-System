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
  joined_at: '2026-09-01T00:00:00Z',
  accounts: [{ account_id: 1001, nickname: 'Inv', equity: 6000, equity_source: 'live' }],
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
const pammEntry = entryFixture({
  id: 700, wallet: 'pamm', amount: 12.5, kind: 'bonus', ref_table: null, ref_id: null,
  note: 'pamm bonus', created_at: '2026-09-18T10:00:00Z', currency: 'USD',
})
const staleRow = entryFixture({
  id: 601, wallet: 'main', amount: 77, kind: 'fee', ref_table: null, ref_id: null,
  note: 'stale load-more row', created_at: '2026-09-17T10:00:00Z', currency: 'USD',
})

/** A `wallet-entries` response under the test's own control, so two
 *  requests can be made to settle out of order. */
function deferredResponse() {
  let resolve!: (payload: unknown) => void
  const promise = new Promise<Response>((res) => {
    resolve = (payload: unknown) => res(jsonResponse(payload))
  })
  return { promise, resolve }
}

/** All pending microtasks (a resolved fetch's `.json()` chain, the
 *  component's `await orgApi(...)`) have had a chance to run, wrapped so a
 *  state update this flush causes -- if the stale-response guard were
 *  missing -- is still act()-safe. */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

function mockRoutes(options: {
  settings?: unknown[]
  refuseAdjustment?: { status: number; body: unknown }
  refuseDelete?: { status: number; body: unknown }
  refuseAdd?: { status: number; body: unknown }
  /** Overrides what `GET .../payment-methods` returns; defaults to `[usdt]`. */
  methods?: unknown[]
  /** Serves `/investors/5/wallet-entries` calls from this queue, in call
   *  order, instead of the default logic -- lets a test control exactly
   *  when and with what each of several concurrent ledger loads answers. */
  walletEntries?: Array<Promise<Response>>
  /** Overrides what `GET .../investors` returns; defaults to `[investor]`. */
  investors?: unknown[]
  /** Makes the link POST answer this instead of 201. */
  refuseLink?: { status: number; body: unknown }
} = {}) {
  let walletEntriesCall = 0
  const queue = options.settings
    ? [...options.settings]
    : [{ withdrawal_min: 0, withdrawal_fee_pct: 0, max_live_accounts: 5 }]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const path = url.split('?')[0]
    if (path.endsWith('/investors')) return jsonResponse(options.investors ?? [investor])
    if (path.endsWith('/investors/5/accounts') && method === 'POST') {
      if (options.refuseLink) return jsonResponse(options.refuseLink.body, options.refuseLink.status)
      return jsonResponse({ user_id: 5, ...JSON.parse(init!.body as string) }, 201)
    }
    if (/\/investors\/5\/accounts\/\d+$/.test(path) && method === 'DELETE') return new Response(null, { status: 204 })
    if (path.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', platform: 'mt5' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', platform: 'mt5' },
        { ctid_trader_account_id: 1003, trader_login: 1003, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
    if (path.endsWith('/payment-methods') && method === 'GET') return jsonResponse(options.methods ?? [usdt])
    if (path.endsWith('/payment-methods') && method === 'POST') {
      if (options.refuseAdd) return jsonResponse(options.refuseAdd.body, options.refuseAdd.status)
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
      if (options.walletEntries) {
        const p = options.walletEntries[walletEntriesCall]
        walletEntriesCall += 1
        return p
      }
      if (url.includes('before=')) return jsonResponse({ entries: [older], has_more: false, next_before: null })
      return jsonResponse({ entries: [entry], has_more: true, next_before: 900 })
    }
    if (path.endsWith('/investors/5/adjustments')) {
      if (options.refuseAdjustment) return jsonResponse(options.refuseAdjustment.body, options.refuseAdjustment.status)
      return jsonResponse({ ...older, id: 901 }, 201)
    }
    if (path.endsWith('/ticket-subjects')) return jsonResponse([])
    if (path.endsWith('/bonus-rules')) {
      return jsonResponse({ signup_enabled: false, signup_amount: 0, kyc_enabled: false, kyc_amount: 0,
        deposit_enabled: false, deposit_pct: 0, deposit_cap: null, updated_at: null })
    }
    if (path.endsWith('/investors/5/bonuses') && method === 'POST') {
      return jsonResponse({ id: 9, source: 'manual', source_id: null, amount: 25, note: 'Promo',
        created_at: '2026-10-05T10:00:00Z', currency: 'USD' }, 201)
    }
    if (path.endsWith('/account-packages')) return jsonResponse([])
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

test("Manage accounts lists the investor's accounts, links one more and unlinks one", async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  expect(screen.queryByLabelText('Account for inv@example.com')).not.toBeInTheDocument()
  await chooseFromMenu('Ada Investor', 'Manage accounts')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's accounts" })
  expect(within(drawer).getByText('Inv')).toBeInTheDocument()
  const picker = within(drawer).getByLabelText('Account to link')
  // Any platform, never one already linked: 1001 is Ada's.
  expect(within(picker).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['1002', '1003'])
  // 1003 has no `platform` field (a cTrader account) and must not be
  // mislabelled "MT5" the way the fulfil picker labels its own options.
  expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['1002 (MT5)', '1003 (CTRADER)'])
  await userEvent.click(within(drawer).getByRole('button', { name: 'Link' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/accounts', 'POST')).toEqual({ account_id: 1002 }))
  expect(await screen.findByText('Account linked')).toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Unlink Inv' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/investors/5/accounts/1001') && (init as RequestInit)?.method === 'DELETE')).toBe(true))
  expect(await screen.findByText('Account unlinked')).toBeInTheDocument()
})

test("the server's refusal to link is shown inside the accounts drawer, not the page banner", async () => {
  mockRoutes({ refuseLink: { status: 409, body: { detail: 'you have reached the limit of 5 live accounts' } } })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Manage accounts')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's accounts" })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Link' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('you have reached the limit of 5 live accounts')
  expect(screen.queryByText('Account linked')).not.toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: "Ada Investor's accounts" })).toBeInTheDocument()
})

test('the accounts column names the first account and counts the rest; equity is their total', async () => {
  mockRoutes({ investors: [{ ...investor, accounts: [
    { account_id: 1001, nickname: 'Inv', equity: 6000, equity_source: 'live' },
    { account_id: 1002, nickname: null, equity: 50, equity_source: 'last known' }] }] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByText('Inv · 2 accounts')).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Accounts' })).toBeInTheDocument()
  expect(screen.getByText('6,050.00')).toBeInTheDocument()
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

function walletEntriesCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([u]) => String(u).includes('/investors/5/wallet-entries'))
}

test('a stale wallet-filter load never overwrites the request that superseded it', async () => {
  // The initial "All wallets" request and the PAMM-filter request both go
  // out; PAMM's answer lands first, then the stale initial answer finally
  // arrives too, with entirely different rows.
  const initial = deferredResponse()
  const pamm = deferredResponse()
  const fetchMock = mockRoutes({ walletEntries: [initial.promise, pamm.promise] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'View ledger')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's ledger" })
  await waitFor(() => expect(walletEntriesCalls(fetchMock)).toHaveLength(1))

  await userEvent.selectOptions(within(drawer).getByLabelText('Wallet'), 'pamm')
  await waitFor(() => expect(walletEntriesCalls(fetchMock)).toHaveLength(2))

  // The newer (PAMM) request settles first ...
  pamm.resolve({ entries: [pammEntry], has_more: false, next_before: null })
  expect(await within(drawer).findByText('pamm bonus')).toBeInTheDocument()

  // ... then the superseded "All wallets" request finally answers. Its
  // answer must be dropped, not overwrite PAMM's rows or its has_more.
  initial.resolve({ entries: [entry], has_more: true, next_before: 900 })
  await flush()
  expect(within(drawer).getByText('pamm bonus')).toBeInTheDocument()
  expect(within(drawer).queryByText('Deposit #11')).not.toBeInTheDocument()
  expect(within(drawer).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
})

test('a superseded Load more never overwrites the request that came after it', async () => {
  // The initial load lands normally (has_more true); Load more is clicked
  // but kept pending; before it answers, the wallet filter changes to
  // PAMM, firing a third request.
  const loadMore = deferredResponse()
  const pamm = deferredResponse()
  const fetchMock = mockRoutes({
    walletEntries: [
      Promise.resolve(jsonResponse({ entries: [entry], has_more: true, next_before: 900 })),
      loadMore.promise,
      pamm.promise,
    ],
  })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'View ledger')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's ledger" })
  await within(drawer).findByText('Deposit #11')

  await userEvent.click(within(drawer).getByRole('button', { name: 'Load more' }))
  await waitFor(() => expect(walletEntriesCalls(fetchMock)).toHaveLength(2))

  await userEvent.selectOptions(within(drawer).getByLabelText('Wallet'), 'pamm')
  await waitFor(() => expect(walletEntriesCalls(fetchMock)).toHaveLength(3))

  // PAMM's fresh load settles first ...
  pamm.resolve({ entries: [pammEntry], has_more: false, next_before: null })
  expect(await within(drawer).findByText('pamm bonus')).toBeInTheDocument()

  // ... then the superseded Load more finally answers. It must not append
  // its row onto what PAMM's load replaced the table with.
  loadMore.resolve({ entries: [staleRow], has_more: false, next_before: null })
  await flush()
  expect(within(drawer).getByText('pamm bonus')).toBeInTheDocument()
  expect(within(drawer).queryByText('stale load-more row')).not.toBeInTheDocument()
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
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
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
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
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

test('editing clears the instructions field by sending an empty string, not null', async () => {
  const withInstructions = methodFixture({
    id: 3, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
    details: { coin: 'USDT', network: 'TRC20', address: 'TXYZ1234567890abcdef' },
    min_amount: 10, fee_pct: 0, instructions: 'Please double-check the memo field.', sort_order: 0,
  })
  const fetchMock = mockRoutes({ methods: [withInstructions] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  await userEvent.click(screen.getByRole('button', { name: 'Edit USDT on TRC20' }))
  const drawer = await screen.findByRole('dialog', { name: 'Edit USDT on TRC20' })
  const instructions = within(drawer).getByLabelText('Instructions')
  expect(instructions).toHaveValue('Please double-check the memo field.')

  // The server treats a missing/null `instructions` on PATCH as "leave
  // unchanged" and "" as "clear" -- an emptied textarea must reach the
  // server as "", or the admin's clear silently does nothing.
  await userEvent.clear(instructions)
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payment-methods/3', 'PATCH')).toEqual({
    label: 'USDT on TRC20', currency: 'USD',
    details: { coin: 'USDT', network: 'TRC20', address: 'TXYZ1234567890abcdef' },
    min_amount: '10', fee_pct: '0', instructions: '', sort_order: 0,
  }))
  expect(await screen.findByText('Payment method saved')).toBeInTheDocument()
})

test('a missing required detail is refused before anything is sent', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  await userEvent.type(within(drawer).getByLabelText('Label'), 'USDT on BEP20')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('Coin is required')
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'POST')).toBe(false)
})

test("the server's refusal is shown inside the open Drawer, not the page banner", async () => {
  const fetchMock = mockRoutes({ refuseAdd: { status: 400, body: { detail: 'address is required' } } })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  // Every client-side-required field is filled in; only the server refuses.
  await userEvent.type(within(drawer).getByLabelText('Label'), 'USDT on BEP20')
  await userEvent.type(within(drawer).getByLabelText('Coin'), 'USDT')
  await userEvent.type(within(drawer).getByLabelText('Network'), 'BEP20')
  await userEvent.type(within(drawer).getByLabelText('Address'), '0xabc123')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('address is required')
  // The Drawer stays open with the refusal inside it; nothing landed on the
  // page-level banner, and the request really was sent (unlike the
  // client-side-refusal test above).
  expect(screen.getByRole('dialog', { name: 'Add payment method' })).toBeInTheDocument()
  expect(screen.queryByText('Payment method added')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/payment-methods') && (init as RequestInit)?.method === 'POST')).toBe(true)
})

test('deleting a method asks first', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
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
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  await userEvent.click(screen.getByRole('button', { name: 'Delete USDT on TRC20' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete USDT on TRC20?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete method' }))
  expect(await screen.findByText('a pending deposit still uses this method')).toBeInTheDocument()
  expect(screen.getByText('USDT on TRC20')).toBeInTheDocument()
})

test('saves the portal settings', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  const save = screen.getByRole('button', { name: 'Save portal settings' })
  expect(save).toBeDisabled()
  await userEvent.clear(screen.getByLabelText('Minimum withdrawal'))
  await userEvent.type(screen.getByLabelText('Minimum withdrawal'), '25')
  await userEvent.clear(screen.getByLabelText('Withdrawal fee %'))
  await userEvent.type(screen.getByLabelText('Withdrawal fee %'), '1.5')
  await userEvent.clear(screen.getByLabelText('Max live accounts per investor'))
  await userEvent.type(screen.getByLabelText('Max live accounts per investor'), '3')
  expect(save).toBeEnabled()
  await userEvent.click(save)
  await waitFor(() => expect(bodyOf(fetchMock, '/portal-settings', 'PUT'))
    .toEqual({ withdrawal_min: '25', withdrawal_fee_pct: '1.5', max_live_accounts: 3 }))
  expect(await screen.findByText('Portal settings saved')).toBeInTheDocument()
})

test('the withdrawal settings follow the server while the form is untouched', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes({ settings: [
    { withdrawal_min: 0, withdrawal_fee_pct: 0, max_live_accounts: 5 },
    { withdrawal_min: 50, withdrawal_fee_pct: 1, max_live_accounts: 5 },
  ] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  fireEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('0')
  expect(screen.getByLabelText('Max live accounts per investor')).toHaveValue('5')
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  await waitFor(() => expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('50'))
  expect(screen.getByLabelText('Withdrawal fee %')).toHaveValue('1')
})

test('a touched settings form survives a poll tick with different server values', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes({ settings: [
    { withdrawal_min: 0, withdrawal_fee_pct: 0, max_live_accounts: 5 },
    { withdrawal_min: 50, withdrawal_fee_pct: 1, max_live_accounts: 5 },
  ] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  fireEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  const minField = screen.getByLabelText('Minimum withdrawal')
  expect(minField).toHaveValue('0')

  // The admin starts editing before the next poll tick lands.
  fireEvent.change(minField, { target: { value: '99' } })
  expect(minField).toHaveValue('99')

  // The poll fires with the server's different values while the edit is
  // still unsaved -- the dirty guard must leave the form exactly as typed,
  // not just "not yet the new server value" but genuinely untouched.
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(minField).toHaveValue('99')
  expect(screen.getByLabelText('Withdrawal fee %')).toHaveValue('0')
})

test('the investors table shows each investor\'s verification, and the packages tab opens', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByRole('columnheader', { name: 'Verification' })).toBeInTheDocument()
  expect(screen.getByText('Verified')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Account packages' }))
  expect(await screen.findByRole('heading', { name: 'Account packages' })).toBeInTheDocument()
  expect(await screen.findByText('No packages yet. Investors cannot request an account until you add one.'))
    .toBeInTheDocument()
})

test('Grant bonus pays into the Credit wallet with the admin MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Grant bonus')
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '25')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'Promo')
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await screen.findByText("Bonus of 25.00 paid to Ada Investor's Credit wallet")).toBeInTheDocument()
  expect(bodyOf(fetchMock, '/investors/5/bonuses', 'POST')).toEqual({ amount: '25', note: 'Promo', mpin: '123456' })
})

test('the Portal settings tab carries the bonus rules and the ticket subjects', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  expect(await screen.findByRole('heading', { name: 'Bonus rules' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Ticket subjects' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Withdrawal and account rules' })).toBeInTheDocument()
})

test('a viewer sees the figures but no actions', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  expect(screen.queryByRole('button', { name: 'Actions for inv@example.com' })).not.toBeInTheDocument()
  expect(screen.getByText('Inv')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Portal settings' }))
  expect(screen.queryByRole('button', { name: 'Add method' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save portal settings' })).not.toBeInTheDocument()
})
