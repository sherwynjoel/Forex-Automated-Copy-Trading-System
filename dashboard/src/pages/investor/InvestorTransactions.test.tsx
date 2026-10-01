import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorTransactions, { entriesQuery, toCsv } from './InvestorTransactions'
import { mockUseOrg } from '../../test/orgMock'
import { entryFixture } from '../../test/portalFixtures'
import type { WalletEntry } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const deposit: WalletEntry = entryFixture({
  id: 50, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})
const withdrawal: WalletEntry = entryFixture({
  id: 40, wallet: 'main', amount: -1000, kind: 'withdrawal', ref_table: 'withdrawals', ref_id: 4,
  note: 'paid to ICICI ••4543', created_at: '2026-09-21T10:00:00Z', currency: 'USD',
})
const adjustment: WalletEntry = entryFixture({
  id: 30, wallet: 'pamm', amount: 12.5, kind: 'adjustment', ref_table: null, ref_id: null,
  note: 'welcome bonus, "phase 4" later', created_at: '2026-09-22T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: { fail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.includes('/investor/wallet-entries')) {
      const q = new URL(url, 'http://x').searchParams
      if (q.get('before') === '40') return jsonResponse({ entries: [adjustment], has_more: false, next_before: null })
      if (q.get('wallet') === 'pamm') return jsonResponse({ entries: [adjustment], has_more: false, next_before: null })
      return jsonResponse({ entries: [deposit, withdrawal], has_more: true, next_before: 40 })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function queries(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/investor/wallet-entries'))
    .map((u) => new URL(u, 'http://x').searchParams)
}

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

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('entriesQuery carries only the filters that are set', () => {
  expect(entriesQuery({ wallet: 'all', kind: '', from: '', to: '', before: null }))
    .toBe('investor/wallet-entries?limit=50')
  expect(entriesQuery({ wallet: 'pamm', kind: 'withdrawal', from: '2026-09-01', to: '2026-09-30', before: 40 }))
    .toBe('investor/wallet-entries?limit=50&wallet=pamm&kind=withdrawal&from=2026-09-01&to=2026-09-30&before=40')
})

test('toCsv writes the six columns, quotes commas and doubles quotes', () => {
  const csv = toCsv([deposit, withdrawal, adjustment])
  const lines = csv.split('\r\n')
  expect(lines[0]).toBe('Date,Wallet,Kind,Amount,Reference,Note')
  expect(lines[1]).toBe('2026-09-20T10:00:00Z,My wallet,deposit,5000.00,deposits/1,')
  expect(lines[2]).toBe('2026-09-21T10:00:00Z,My wallet,withdrawal,-1000.00,withdrawals/4,paid to ICICI ••4543')
  expect(lines[3]).toBe('2026-09-22T10:00:00Z,PAMM wallet,adjustment,12.50,,"welcome bonus, ""phase 4"" later"')
  expect(lines[4]).toBe('')
})

test('toCsv neutralises formula cells but keeps a negative amount a plain number', () => {
  const evil = entryFixture({
    ...withdrawal, note: '=HYPERLINK("http://x","click")', kind: '+cmd', ref_table: null,
  })
  const lines = toCsv([evil, { ...adjustment, note: '@SUM(A1)' }, { ...adjustment, note: '-2+3' },
                       { ...adjustment, note: '\tTAB' }]).split('\r\n')
  expect(lines[1]).toBe(`2026-09-21T10:00:00Z,My wallet,'+cmd,-1000.00,,"'=HYPERLINK(""http://x"",""click"")"`)
  expect(lines[2].endsWith(",12.50,,'@SUM(A1)")).toBe(true)
  expect(lines[3].endsWith(",12.50,,'-2+3")).toBe(true)
  expect(lines[4].endsWith(",12.50,,'\tTAB")).toBe(true)
})

test('lists entries with a signed amount and a link to the request, and has its heading and title', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Transactions' })).toBeInTheDocument()
  expect(document.title).toBe('Transactions · MirrorFleet')
  expect(await screen.findByRole('link', { name: 'Deposit #1' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'Withdrawal #4' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByText(/-1,000\.00/)).toBeInTheDocument()
  expect(screen.getAllByText('My wallet').length).toBeGreaterThan(0)
  expect(screen.getByText('paid to ICICI ••4543')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Credit wallet' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Social wallet' })).toBeInTheDocument()
})

test('a wallet tab reloads with that wallet', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('tab', { name: 'PAMM wallet' }))
  // The note is the one text only the adjustment row carries: 'Adjustment'
  // also sits in the Kind filter's <option> from the first paint.
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(within(screen.getByRole('table')).getAllByText('Adjustment').length).toBeGreaterThan(0)
  expect(screen.queryByRole('link', { name: 'Deposit #1' })).not.toBeInTheDocument()
  const last = queries(fetchMock).at(-1)!
  expect(last.get('wallet')).toBe('pamm')
  expect(last.get('before')).toBeNull()
})

test('a stale tab load never overwrites the request that superseded it', async () => {
  // The initial 'all' request and the PAMM tab's request both go out; PAMM's
  // answer lands first, then the stale 'all' answer finally arrives too.
  const initial = deferredResponse()
  const pamm = deferredResponse()
  const queue = [initial, pamm]
  let call = 0
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/investor/wallet-entries')) {
      const d = queue[call]
      call += 1
      return d.promise
    }
    return Promise.resolve(jsonResponse({}))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

  await userEvent.click(screen.getByRole('tab', { name: 'PAMM wallet' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

  // The newer (PAMM) request settles first ...
  pamm.resolve({ entries: [adjustment], has_more: false, next_before: null })
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()

  // ... then the superseded 'all' request finally answers, with different
  // rows entirely. Its answer must be dropped, not overwrite PAMM's rows.
  initial.resolve({ entries: [deposit, withdrawal], has_more: true, next_before: 40 })
  await flush()
  expect(screen.getByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Deposit #1' })).not.toBeInTheDocument()
})

test('a superseded Load more never overwrites the request that came after it', async () => {
  // 'all' loads, then Load more is clicked (before=40) but kept pending;
  // before it answers, the PAMM tab is clicked, firing a third request.
  const staleRow: WalletEntry = entryFixture({
    id: 99, wallet: 'main', amount: 77, kind: 'fee', ref_table: null, ref_id: null,
    note: 'stale load-more row', created_at: '2026-09-23T10:00:00Z', currency: 'USD',
  })
  const loadMore = deferredResponse()
  const pamm = deferredResponse()
  const queue = [
    Promise.resolve(jsonResponse({ entries: [deposit, withdrawal], has_more: true, next_before: 40 })),
    loadMore.promise,
    pamm.promise,
  ]
  let call = 0
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/investor/wallet-entries')) {
      const p = queue[call]
      call += 1
      return p
    }
    return Promise.resolve(jsonResponse({}))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })

  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

  await userEvent.click(screen.getByRole('tab', { name: 'PAMM wallet' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))

  // PAMM's fresh load settles first ...
  pamm.resolve({ entries: [adjustment], has_more: false, next_before: null })
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()

  // ... then the superseded Load more finally answers. It must not append
  // its row onto what PAMM's load replaced the table with.
  loadMore.resolve({ entries: [staleRow], has_more: false, next_before: null })
  await flush()
  expect(screen.getByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(screen.queryByText('stale load-more row')).not.toBeInTheDocument()
})

test('the date and kind filters are sent only after Apply, and Clear drops them', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.type(screen.getByLabelText('From date'), '2026-09-01')
  await userEvent.type(screen.getByLabelText('To date'), '2026-09-30')
  await userEvent.selectOptions(screen.getByLabelText('Kind'), 'withdrawal')
  expect(queries(fetchMock)).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(queries(fetchMock)).toHaveLength(2))
  const applied = queries(fetchMock)[1]
  expect(applied.get('from')).toBe('2026-09-01')
  expect(applied.get('to')).toBe('2026-09-30')
  expect(applied.get('kind')).toBe('withdrawal')
  await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
  await waitFor(() => expect(queries(fetchMock)).toHaveLength(3))
  expect(queries(fetchMock)[2].get('kind')).toBeNull()
  expect(screen.getByLabelText('Kind')).toHaveValue('')
})

test('Load more appends the next page using next_before', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Deposit #1' })).toBeInTheDocument()
  expect(queries(fetchMock).at(-1)!.get('before')).toBe('40')
  expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
})

test('Download CSV hands the browser a transactions.csv built from the loaded rows', async () => {
  mockRoutes()
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:csv')
  const revokeObjectURL = vi.fn()
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
  let downloaded: HTMLAnchorElement | null = null
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloaded = this
  })
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
  expect(createObjectURL).toHaveBeenCalledTimes(1)
  expect(createObjectURL.mock.calls[0][0].type).toContain('text/csv')
  expect(downloaded).not.toBeNull()
  expect(downloaded!.download).toBe('transactions.csv')
  expect(downloaded!.href).toContain('blob:csv')
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:csv')
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No transactions yet')).toBeInTheDocument()
})
