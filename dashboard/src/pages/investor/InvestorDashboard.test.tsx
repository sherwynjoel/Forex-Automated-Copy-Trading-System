import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDashboard, { flowWindow, greeting, last7 } from './InvestorDashboard'
import { mockUseOrg } from '../../test/orgMock'
import { accountSummaryFixture, entryFixture, summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
// NextStep reads LANDING_FACTS; pin it so the real support address cannot
// make the "no contact" case pass or fail by accident.
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const DAY = 24 * 3600 * 1000
const daysAgo = (n: number) => new Date(Date.now() - n * DAY).toISOString().slice(0, 10)
const zero = { balance: 0, on_hold: 0, available: 0 }

const linked: InvestorSummary = {
  ...summaryFixture(),
  org: { id: 1, name: 'Desk' }, currency: 'USD',
  investor: { display_name: 'Sherwyn Joel', first_name: 'Sherwyn', member_since: '2026-09-01T00:00:00Z' },
  wallets: { main: { balance: 5120.5, on_hold: 100, available: 5020.5 }, credit: zero, pamm: zero, social: zero },
  totals: { deposited: 5000, withdrawn: 0, transferred_in: 0, transferred_out: 3000 },
  cash_flow: [
    { date: daysAgo(3), deposits: 5000, withdrawals: 0 },
    { date: daysAgo(40), deposits: 0, withdrawals: 1000 },
  ],
  pending: { deposits: 0, withdrawals: 2, transfers: 0, payout_destinations: 1 },
  deposits_open: true, withdrawal_rules: { min: 0, fee_pct: 0 },
  accounts: [accountSummaryFixture({
    account_id: 1001, nickname: 'Inv', mt5_login: null, mt5_server: null, equity: 5120.5,
    net_funded: 3000, profit: 2120.5, account_available: 5120.5, open_positions: 1,
  })],
  account_limit: { max: 5, used: 1 },
  equity_source: 'live', equity: 5120.5, net_funded: 3000, profit: 2120.5, open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, accounts: [], account_limit: { max: 5, used: 0 }, equity_source: 'unknown',
  equity: null, profit: null, open_positions: 0,
}
const entry = entryFixture({
  id: 1, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})

function mockRoutes(summaries: InvestorSummary | InvestorSummary[], entries: unknown[] = [entry], fail = false) {
  const queue = Array.isArray(summaries) ? [...summaries] : [summaries]
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
    if (url.includes('/investor/wallet-entries')) {
      return jsonResponse({ entries, has_more: false, next_before: null })
    }
    return jsonResponse({})
  }))
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
// Unmount first: setHidden(false) broadcasts to every subscriber, and a
// still-mounted page would update outside act().
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  setHidden(false)
  facts.supportEmail = ''
})

test.each([[9, 'Good morning'], [15, 'Good afternoon'], [20, 'Good evening']])(
  'hour %i greets with "%s"', (hour, text) => {
    expect(greeting(hour)).toBe(text)
  })

test('flowWindow keeps the last N days oldest first; last7 fills quiet days with zero', () => {
  const now = Date.parse('2026-09-29T12:00:00Z')
  const flow = [
    { date: '2026-09-29', deposits: 10, withdrawals: 0 },
    { date: '2026-09-25', deposits: 0, withdrawals: 4 },
    { date: '2026-08-01', deposits: 100, withdrawals: 0 },
  ]
  expect(flowWindow(flow, 7, now).map((f) => f.date)).toEqual(['2026-09-25', '2026-09-29'])
  expect(flowWindow(flow, 90, now)).toHaveLength(3)
  expect(last7(flow, (f) => f.deposits - f.withdrawals, now)).toEqual([0, 0, -4, 0, 0, 0, 10])
})

test('the page has its heading and title, greets by the hour, and shows Loading until the summary lands', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 8, 29, 15, 0, 0))
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  // Loading announces through aria-label; its skeleton bars carry no text.
  expect(screen.getByRole('status')).toHaveAccessibleName(/loading/i)
  expect(await screen.findByText('Good afternoon, Sherwyn')).toBeInTheDocument()
  expect(document.title).toBe('Dashboard · MirrorFleet')
})

test('linked investors see the balance, the tiles, the account card, pending lines and recent activity', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  // The big figure and the My wallet tile both show the floored available.
  expect((await screen.findAllByText('5,020.50 USD')).length).toBeGreaterThan(0)
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByText('Total deposited')).toBeInTheDocument()
  expect(screen.getAllByText('5,000.00 USD').length).toBeGreaterThan(0)
  expect(screen.getByRole('link', { name: 'Inv' })).toHaveAttribute('href', '/org/1/invest/account?account=1001')
  expect(screen.getByText('connected')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open live account' })).toHaveAttribute('href', '/org/1/invest/open-account')
  expect(screen.getByText('2 withdrawals in progress')).toBeInTheDocument()
  expect(screen.getByText('1 payout account awaiting approval')).toBeInTheDocument()
  expect(screen.queryByText(/deposits? awaiting confirmation/)).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Withdrawals' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByText('Deposit #1')).toBeInTheDocument()
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Deposit' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/org/1/invest/transactions')
})

test('Hide balances masks every figure and flips to Show balances', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  await screen.findAllByText('5,020.50 USD')
  await userEvent.click(screen.getByRole('button', { name: 'Hide balances' }))
  expect(screen.queryByText('5,020.50 USD')).not.toBeInTheDocument()
  expect(screen.getAllByText('••••').length).toBeGreaterThan(3)
  expect(screen.getByRole('button', { name: 'Show balances' })).toHaveAttribute('aria-pressed', 'true')
})

test('Hide balances also removes every chart -- a sparkline or the cash-flow bars can leak shape and timing even with the figures masked', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  // Shown: the three sparklines and the cash-flow bars are all present.
  expect(await screen.findByRole('img', { name: 'Net flow, last 7 days' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Deposits, last 7 days' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Withdrawals, last 7 days' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Deposits and withdrawals by day' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Hide balances' }))

  expect(screen.queryByRole('img', { name: 'Net flow, last 7 days' })).not.toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'Deposits, last 7 days' })).not.toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'Withdrawals, last 7 days' })).not.toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'Deposits and withdrawals by day' })).not.toBeInTheDocument()
  expect(screen.getByText('Chart hidden while balances are hidden')).toBeInTheDocument()
  // The tabs and the masked totals strip stay put.
  expect(screen.getByRole('tab', { name: '30D' })).toBeInTheDocument()
  expect(screen.getAllByText('••••').length).toBeGreaterThan(3)
})

test('unlinked investors see the setup notice with the new copy and no account card', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText(/deposits, withdrawals and wallet transfers work now/i)).toBeInTheDocument()
  expect(screen.queryByText('Inv')).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Live accounts' })).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the setup notice shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the cash flow card switches range with the 7D / 30D / 90D tabs', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const panel = await screen.findByRole('tabpanel')
  expect(screen.getByRole('tab', { name: '30D' })).toHaveAttribute('aria-selected', 'true')
  // 30 days back: the 5,000 deposit is in, the 1,000 withdrawal (40 days) is not.
  expect(within(panel).getByText('0.00 USD')).toBeInTheDocument()
  expect(within(panel).getByRole('img', { name: 'Deposits and withdrawals by day' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '90D' }))
  expect(await within(panel).findByText('1,000.00 USD')).toBeInTheDocument()
  expect(within(panel).queryByText('0.00 USD')).not.toBeInTheDocument()
})

test('the account card goes away when the account is unlinked later', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes([linked, unlinked])
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(await screen.findByText('Inv')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('Inv')).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes(linked, [], true)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Nothing yet')).toBeInTheDocument()
})

test('the verification card sends an unverified investor to their profile', async () => {
  mockRoutes({ ...summaryFixture(), kyc_status: 'draft' })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Identity verification' })).closest('section')!
  expect(within(card).getByText('Not submitted')).toBeInTheDocument()
  expect(within(card).getByRole('link', { name: 'Verify now' })).toHaveAttribute('href', '/org/1/invest/profile')
})

test('a verified investor without an account is offered Open account', async () => {
  mockRoutes({ ...summaryFixture(), kyc_status: 'approved', accounts: [], account_limit: { max: 5, used: 0 } })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const card = (await screen.findByRole('heading', { name: 'Identity verification' })).closest('section')!
  expect(within(card).getByRole('link', { name: 'Open account' })).toHaveAttribute('href', '/org/1/invest/open-account')
})

test('several accounts each get a row in the tray with the total; at the cap Open live account goes', async () => {
  mockRoutes({
    ...linked,
    accounts: [
      accountSummaryFixture({ account_id: 1001, mt5_login: 5001, equity: 700, profit: 100, connected: true }),
      accountSummaryFixture({ account_id: 1002, mt5_login: null, nickname: 'Swing', equity: 250, profit: -50, connected: false }),
    ],
    account_limit: { max: 2, used: 2 }, equity: 950, equity_source: 'last known',
  })
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const tray = (await screen.findByRole('heading', { name: 'Live accounts' })).closest('section')!
  expect(within(tray).getByRole('link', { name: 'MT5 5001' })).toHaveAttribute('href', '/org/1/invest/account?account=1001')
  expect(within(tray).getByRole('link', { name: 'Swing' })).toHaveAttribute('href', '/org/1/invest/account?account=1002')
  expect(within(tray).getByText('terminal offline')).toBeInTheDocument()
  expect(within(tray).getByText('950.00 USD')).toBeInTheDocument()
  expect(within(tray).getByText(/last known/)).toBeInTheDocument()
  expect(within(tray).queryByRole('link', { name: 'Open live account' })).not.toBeInTheDocument()
})
