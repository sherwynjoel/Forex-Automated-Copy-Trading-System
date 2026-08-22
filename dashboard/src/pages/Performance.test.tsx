import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Performance from './Performance'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const accounts = [
  {
    ctid_trader_account_id: 100, trader_login: 90100, is_live: false, role: 'master',
    enabled: true, multiplier: 1.0, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null,
  },
  {
    ctid_trader_account_id: 101, trader_login: 90101, is_live: false, role: 'slave',
    enabled: true, multiplier: 1.0, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null,
  },
]

const analytics = {
  closed_trades: 3, wins: 2, losses: 1, win_rate: 2 / 3,
  profit_factor: 4.0, best_trade: 100.0, worst_trade: -40.0,
  avg_win: 80.0, avg_loss: -40.0, net_pnl: 116.7,
  gross_wins: 160.0, gross_losses: -40.0,
  max_drawdown: 400.0, max_drawdown_pct: 0.0396,
  equity_curve: [
    { timestamp: 1700000000000, balance: 10100 },
    { timestamp: 1700100000000, balance: 9800 },
    { timestamp: 1700200000000, balance: 10200 },
  ],
  per_symbol: [
    { symbol: 'EURUSD', trades: 2, gross_pnl: 160.0 },
    { symbol: 'GBPUSD', trades: 1, gross_pnl: -40.0 },
  ],
  weekly: [
    { week_start: 1699833600000, trades: 2, gross_pnl: 60.0 },
    { week_start: 1700438400000, trades: 1, gross_pnl: 60.0 },
  ],
  weeks: 4, truncated: false,
}

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/analytics')) return respond(analytics)
    if (url.includes('/api/orgs/1/accounts')) return respond(accounts)
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  return render(
    <MemoryRouter>
      <Performance />
    </MemoryRouter>
  )
}

test('loads master analytics by default and shows the headline stats', async () => {
  const fetchMock = mockRoutes()
  renderPage()

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/orgs/1/accounts/100/analytics?weeks=4'))).toBe(true)
  })

  expect(await screen.findByText('+116.70')).toBeInTheDocument()      // net P&L
  expect(screen.getByText('66.7%')).toBeInTheDocument()               // win rate
  expect(screen.getByText(/2W · 1L/)).toBeInTheDocument()             // W/L
  expect(screen.getByText('4.00')).toBeInTheDocument()                // profit factor
  expect(screen.getByText('+100.00')).toBeInTheDocument()             // best trade
  // worst trade (-40.00 also appears in avg-loss footer and symbol table)
  expect(screen.getAllByText('-40.00').length).toBeGreaterThan(0)
})

test('renders the equity curve and weekly bars as SVG', async () => {
  mockRoutes()
  const { container } = renderPage()

  await screen.findByText('+116.70')
  expect(container.querySelector('[data-chart="equity-curve"] svg')).toBeTruthy()
  expect(container.querySelector('[data-chart="weekly-pnl"] svg')).toBeTruthy()
})

test('per-symbol table lists both symbols', async () => {
  mockRoutes()
  renderPage()

  await screen.findByText('+116.70')
  expect(screen.getByText('EURUSD')).toBeInTheDocument()
  expect(screen.getByText('GBPUSD')).toBeInTheDocument()
})

test('changing the range refetches with new weeks', async () => {
  const fetchMock = mockRoutes()
  renderPage()

  await screen.findByText('+116.70')
  await userEvent.selectOptions(screen.getByLabelText(/range/i), '12')

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/analytics?weeks=12'))).toBe(true)
  })
})

test('the range control stops at the API ceiling of 12 weeks', async () => {
  // Each week is one sequential broker request on the wire every org
  // shares, so the dropdown must not offer a range the API would clamp
  // anyway (routes/insights.py:MAX_ANALYTICS_WEEKS).
  mockRoutes()
  renderPage()

  await screen.findByText('+116.70')
  const options = Array.from(
    (screen.getByLabelText(/range/i) as HTMLSelectElement).options,
  ).map((o) => Number(o.value))

  expect(options).toEqual([4, 8, 12])
  expect(Math.max(...options)).toBeLessThanOrEqual(12)
})

test('changing the account refetches its analytics', async () => {
  const fetchMock = mockRoutes()
  renderPage()

  await screen.findByText('+116.70')
  await userEvent.selectOptions(screen.getByLabelText(/account/i), '101')

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/orgs/1/accounts/101/analytics'))).toBe(true)
  })
})

test('empty analytics shows a helpful empty state', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/analytics')) {
      return respond({
        closed_trades: 0, wins: 0, losses: 0, win_rate: null,
        profit_factor: null, best_trade: null, worst_trade: null,
        avg_win: null, avg_loss: null, net_pnl: 0, gross_wins: 0, gross_losses: 0,
        max_drawdown: 0, max_drawdown_pct: 0, equity_curve: [],
        per_symbol: [], weekly: [], weeks: 4, truncated: false,
      })
    }
    if (url.includes('/api/orgs/1/accounts')) return respond(accounts)
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()

  expect(await screen.findByText(/no closed trades/i)).toBeInTheDocument()
})

test('the analytics cards moved here from Overview render for the selected account', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('owner'))
  mockRoutes()

  const { container } = render(
    <MemoryRouter>
      <Performance />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/mirrorfleet score/i)).toBeInTheDocument()
  })
  expect(container.querySelector('[data-chart="cumulative-pnl"] svg')).toBeTruthy()
  expect(container.querySelector('[data-chart="daily-pnl"] svg')).toBeTruthy()
  // This curve dips 10100 -> 9800, so the drawdown card draws rather than
  // reporting a clean window.
  expect(container.querySelector('[data-chart="drawdown"] svg')).toBeTruthy()
  expect(screen.getByText(/p&l by day/i)).toBeInTheDocument()
})

test('activity windows appear as tiles without repeating Net P&L', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('owner'))
  mockRoutes()

  render(
    <MemoryRouter>
      <Performance />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/today's trades/i)).toBeInTheDocument()
  })
  expect(screen.getByText(/this week/i)).toBeInTheDocument()
  // Net P&L already had a tile here; the move must not duplicate it.
  expect(screen.getAllByText(/^Net P&L$/i)).toHaveLength(1)
})
