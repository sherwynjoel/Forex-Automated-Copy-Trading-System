// src/pages/investor/InvestorOverview.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorOverview from './InvestorOverview'
import * as apiModule from '../../lib/api'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

// The support line reads LANDING_FACTS; pin it so Task 5's real address
// cannot make the "no contact" case pass or fail by accident.
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

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

const linked = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true,
  total_deposited: 5000, total_withdrawn: 0, net_deposits: 5000, pending_withdrawn: 0,
  equity: 5120.5, profit: 120.5, available: 5120.5,
}
const unlinked = { ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
                   equity: null, profit: null, available: null, net_deposits: 0, total_deposited: 0 }

const deposit = { id: 1, user_id: 1, account_id: 1001, amount: 5000, coin: 'USDT', txid: 'abc',
                  note: null, status: 'confirmed', decided_by: 2, decided_at: '2026-09-20T11:00:00Z',
                  decision_note: null, created_at: '2026-09-20T10:00:00Z' }
const withdrawal = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                     status: 'requested', equity_at_request: 5120.5, equity_verified: true,
                     decided_by: null, decided_at: null, decision_note: null, paid_by: null,
                     paid_at: null, txid: null, created_at: '2026-09-22T10:00:00Z' }

function mockRoutes(summaries: unknown | unknown[],
                    ledger: { deposits?: unknown[]; withdrawals?: unknown[] } = {}) {
  const queue = Array.isArray(summaries) ? [...summaries] : [summaries]
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
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
    if (url.endsWith('/investor/deposits')) return jsonResponse(ledger.deposits ?? [])
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(ledger.withdrawals ?? [])
    return jsonResponse({})
  }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
})
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('the page has its heading and title, and shows Loading until the summary lands', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
  // Loading announces through aria-label; its skeleton bars carry no text.
  expect(screen.getByRole('status')).toHaveAccessibleName(/loading/i)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(document.title).toBe('Overview · MirrorFleet')
})

test('unlinked investors see the setup notice and no figures', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText(/you can file your deposit notice on the deposit page now/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('without a wallet the setup notice says what happens next', async () => {
  mockRoutes({ ...unlinked, wallet_configured: false })
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(
    "Your admin links your trading account; deposits open once the workspace's wallet is set.",
  )).toBeInTheDocument()
})

test('the setup notice shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('linked investors see equity, profit, positions and the snapshot, each with a unit', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  // Equity and profit are account-currency figures (USD default).
  expect((await screen.findAllByText('5,120.50 USD')).length).toBeGreaterThan(0)
  expect((await screen.findAllByText('120.50 USD')).length).toBeGreaterThan(0)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(await screen.findByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getAllByText(/live/i).length).toBeGreaterThan(0)
  expect(screen.getByText('5,120.50 USD available')).toBeInTheDocument()
})

test('recent activity carries the ledger coin and a fluid date column', async () => {
  mockRoutes(linked, { deposits: [deposit], withdrawals: [withdrawal] })
  const { container } = render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText('+5,000.00 USDT')).toBeInTheDocument()
  expect(screen.getByText('-1,000.00 USDT')).toBeInTheDocument()
  // Ledger tiles use the same coin as the rows they add up.
  expect(screen.getByText('5,000.00 USDT')).toBeInTheDocument()
  const when = container.querySelector('li time')
  expect(when).not.toBeNull()
  expect(when).not.toHaveClass('w-40')
  expect(when).toHaveClass('min-w-0')
})

test('positions and the snapshot go away when the account is unlinked later', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes([linked, unlinked])
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(10000)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/66\.7%/)).not.toBeInTheDocument()
})
