import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'
import { summaryFixture } from '../../test/portalFixtures'
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
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); facts.supportEmail = '' })

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

test('unlinked investors see the setup notice instead of positions', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/66\.7%/)).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})
