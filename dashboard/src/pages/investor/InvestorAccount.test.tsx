import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'
import { summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', equity: 5120.5, net_funded: 5000, profit: 120.5, account_available: 5120.5, open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
  equity: null, profit: null, account_available: null, open_positions: 0,
}

function mockRoutes(summary: InvestorSummary) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.endsWith('/investor/positions')) {
      return jsonResponse({ equity_source: 'live', positions: [
        { position_id: 7, symbol: 'XAUUSD', side: 'BUY', volume: 1, entry_price: 4350,
          current_price: 4360, stop_loss: 4300, take_profit: 4400, pnl_quote: 10 }] })
    }
    if (url.includes('/investor/analytics')) {
      return jsonResponse({ closed_trades: 3, wins: 2, losses: 1, win_rate: 66.7,
        profit_factor: 2.1, best_trade: 50, worst_trade: -20, avg_win: 40, avg_loss: -20,
        net_pnl: 120.5, gross_wins: 140.5, gross_losses: 20, max_drawdown: 30,
        max_drawdown_pct: 0.6, equity_curve: [{ timestamp: 1, balance: 5000 },
        { timestamp: 2, balance: 5120.5 }], per_symbol: [], weekly: [], weeks: 4, truncated: false })
    }
    return jsonResponse({})
  }))
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
// Unmount before resetting the hide-balances store: Vitest runs afterEach
// hooks in reverse order, so RTL's auto-cleanup (registered before this
// file's hooks run) would otherwise fire AFTER setHidden(false) here, while
// the tree is still mounted -- the broadcast then updates Money/StatTile
// outside act(). Explicit cleanup() first makes the unmount happen before
// the store changes, no matter what order the hooks run in.
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); facts.supportEmail = ''; setHidden(false) })

test('the Account page shows who you are, the login forms and its title', async () => {
  mockRoutes(linked)
  // AccountSecurity calls useNavigate, so the page needs a router.
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument()
  expect(screen.getByText('Test User')).toBeInTheDocument()
  expect(screen.getByText('user@example.com')).toBeInTheDocument()
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(screen.getByText('Investor')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Change MPIN' })).toBeInTheDocument()
  expect(document.title).toBe('Account · MirrorFleet')
  await screen.findByText('XAUUSD')
})

test('linked investors see the trading account, open positions and the 4-week snapshot', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(screen.getByText('Inv')).toBeInTheDocument()
  expect(screen.getAllByText('5,120.50 USD').length).toBeGreaterThan(0)
  expect(screen.getAllByText('120.50 USD').length).toBeGreaterThan(0)
  expect(screen.getByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Equity curve' })).toBeInTheDocument()
  expect(screen.getByText('Live P&L (quote currency)')).toBeInTheDocument()
})

test('hiding balances masks every money figure, drops toned colour and replaces the equity curve', async () => {
  mockRoutes(linked)
  setHidden(true)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  await screen.findByText('XAUUSD')

  // The equity curve -- a chart of money values -- is dropped entirely,
  // replaced by a neutral placeholder line, not just recolored.
  expect(screen.queryByRole('img', { name: 'Equity curve' })).not.toBeInTheDocument()
  expect(screen.getByText('Chart hidden while balances are hidden')).toBeInTheDocument()

  // Every Money-backed figure on the page reads as masked dots.
  const masked = screen.getAllByRole('img', { name: 'Hidden amount' })
  expect(masked.length).toBeGreaterThan(0)
  expect(screen.queryByText('5,120.50 USD')).not.toBeInTheDocument()
  expect(screen.queryByText('120.50 USD')).not.toBeInTheDocument()
  // Win rate is a percentage, not a money figure -- it is not masked.
  expect(screen.getByText(/66\.7%/)).toBeInTheDocument()

  // No masked figure anywhere on the page carries a profit/loss colour that
  // would give the sign away through the dots themselves.
  for (const m of masked) {
    expect(m.className).not.toMatch(/\btext-(profit|loss)(-deep)?\b/)
  }

  // The Net P&L tile: StatTile's value is a plain string (not a <Money>),
  // masked and detoned by AnalyticsPanel itself -- checked on its own.
  const netPnlValue = screen.getByText('Net P&L').nextElementSibling as HTMLElement
  expect(netPnlValue).toHaveTextContent('••••')
  expect(netPnlValue.className).not.toMatch(/\btext-(profit|loss)(-deep)?\b/)

  // The Profit row on the trading-account card: its <dd> still carries a
  // tone class computed from the real value, but nothing renders that
  // color -- the masked <Money> inside overrides it on the element itself.
  const profitDd = screen.getByText('Profit').nextElementSibling as HTMLElement
  const profitMasked = within(profitDd).getByRole('img', { name: 'Hidden amount' })
  expect(profitMasked.className).not.toMatch(/\btext-(profit|loss)(-deep)?\b/)
})

test('unlinked investors see the setup notice instead of positions', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/66\.7%/)).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})
