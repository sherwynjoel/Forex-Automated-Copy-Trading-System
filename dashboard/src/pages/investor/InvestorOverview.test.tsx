// src/pages/investor/InvestorOverview.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorOverview from './InvestorOverview'
import * as apiModule from '../../lib/api'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

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

function mockRoutes(summaries: unknown | unknown[]) {
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
    if (url.endsWith('/investor/deposits') || url.endsWith('/investor/withdrawals')) return jsonResponse([])
    return jsonResponse({})
  }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('unlinked investors see the setup notice and no figures', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
})

test('linked investors see equity, profit, positions and the snapshot', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect((await screen.findAllByText('5,120.50')).length).toBeGreaterThan(0)
  expect((await screen.findAllByText('120.50')).length).toBeGreaterThan(0)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(await screen.findByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getAllByText(/live/i).length).toBeGreaterThan(0)
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
