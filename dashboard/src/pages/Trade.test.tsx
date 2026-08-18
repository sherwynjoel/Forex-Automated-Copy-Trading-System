import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Trade from './Trade'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function setRole(role: Role) {
  useOrgMock.mockReturnValue(mockUseOrg(role))
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const accounts = [
  {
    ctid_trader_account_id: 100, trader_login: 90100, is_live: false, role: 'master',
    enabled: true, multiplier: 1.0, status: 'ok', last_error: null,
    connection_status: 'active', nickname: 'Main',
  },
  {
    ctid_trader_account_id: 101, trader_login: 90101, is_live: false, role: 'slave',
    enabled: true, multiplier: 1.0, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null,
  },
]

const symbols = [
  { name: 'EURUSD', symbol_id: 1, digits: 5, min_volume_lots: 0.01, step_volume_lots: 0.01 },
  { name: 'GBPUSD', symbol_id: 2, digits: 5, min_volume_lots: 0.01, step_volume_lots: 0.01 },
]

const details = {
  account_id: 100, trader_login: 90100, balance: 10000, deposit_currency: 'USD',
  open_positions: [
    {
      position_id: 7001, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
      volume: 200000, volume_lots: '0.02', price: 1.105, label: '',
      stop_loss: null, take_profit: null, swap: 0, open_timestamp: null,
    },
  ],
  pending_orders: [
    {
      order_id: 9001, symbol_id: 1, symbol: 'EURUSD', side: 'SELL',
      volume: 100000, volume_lots: '0.01', order_type: 'LIMIT',
      limit_price: 1.2, stop_price: null, label: '',
    },
  ],
}

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/margin-estimate')) {
      return respond({ symbol: 'EURUSD', volume: 5000000, volume_lots: '0.50',
                       buy_margin: 13.75, sell_margin: 13.75 })
    }
    if (url.includes('/trendbars')) {
      return respond({ symbol: 'EURUSD', period: 'H1', bars: [
        { timestamp: 1700000000000, open: 1.1, high: 1.11, low: 1.095, close: 1.105, volume: 10 },
        { timestamp: 1700003600000, open: 1.105, high: 1.12, low: 1.1, close: 1.11, volume: 12 },
      ] })
    }
    if (url.includes('/symbols')) return respond(symbols)
    if (url.includes('/details')) return respond(details)
    if (url.includes('/api/orgs/1/accounts')) return respond(accounts)
    if (url.includes('/api/orgs/1/orders/cancel')) return respond({ status: 'submitted' })
    if (url.includes('/api/orgs/1/orders')) return respond({ status: 'submitted', volume: 5000000 })
    if (url.includes('/api/orgs/1/positions/close')) return respond({ status: 'submitted' })
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderTrade() {
  return render(
    <MemoryRouter>
      <Trade />
    </MemoryRouter>
  )
}

test('defaults to the master account and loads its symbols', async () => {
  setRole('trader')
  mockRoutes()
  renderTrade()

  const select = await screen.findByLabelText(/account/i)
  await waitFor(() => {
    expect((select as HTMLSelectElement).value).toBe('100')
  })
  // Symbol picker defaults to the first cached symbol
  const symbolBox = await screen.findByLabelText(/symbol/i)
  await waitFor(() => {
    expect((symbolBox as HTMLInputElement).value).toBe('EURUSD')
  })
})

test('symbol picker searches and selects manually', async () => {
  mockRoutes()
  renderTrade()

  const symbolBox = await screen.findByLabelText(/symbol/i)
  await waitFor(() => {
    expect((symbolBox as HTMLInputElement).value).toBe('EURUSD')
  })

  // Type to search: GBP narrows the list; picking it selects it.
  await userEvent.clear(symbolBox)
  await userEvent.type(symbolBox, 'gbp')
  const option = await screen.findByRole('option', { name: 'GBPUSD' })
  await userEvent.click(option)

  expect((symbolBox as HTMLInputElement).value).toBe('GBPUSD')
})

test('shows the margin estimate for the ticket', async () => {
  mockRoutes()
  renderTrade()

  await screen.findByLabelText(/symbol/i)
  expect(await screen.findByText(/margin required/i)).toBeInTheDocument()
  const values = await screen.findAllByText('13.75')
  expect(values.length).toBe(2)  // buy and sell
})

test('the margin and candle reads are org-scoped', async () => {
  setRole('trader')
  const fetchMock = mockRoutes()
  renderTrade()

  await screen.findByText(/margin required/i)
  await waitFor(() => {
    const margin = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/margin-estimate'))
    const bars = fetchMock.mock.calls.find(([u]) => String(u).includes('/trendbars'))
    expect(String(margin![0])).toContain('/api/orgs/1/accounts/100/margin-estimate')
    expect(String(bars![0])).toContain('/api/orgs/1/accounts/100/trendbars')
  })
})

test('renders the price chart for the selected symbol', async () => {
  mockRoutes()
  const { container } = renderTrade()

  await screen.findByLabelText(/symbol/i)
  await waitFor(() => {
    expect(container.querySelector('[data-chart="candles"] svg')).toBeTruthy()
  })
})

test('placing a market order confirms then POSTs /api/orgs/1/orders', async () => {
  setRole('trader')
  const fetchMock = mockRoutes()
  renderTrade()

  await waitFor(() => {
    expect((screen.getByLabelText(/symbol/i) as HTMLInputElement).value).toBe('EURUSD')
  })

  await userEvent.clear(screen.getByLabelText(/volume/i))
  await userEvent.type(screen.getByLabelText(/volume/i), '0.5')
  await userEvent.click(screen.getByRole('button', { name: /^sell/i }))
  await userEvent.click(screen.getByRole('button', { name: /review order/i }))

  // Confirmation dialog summarises, then submits on confirm
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(/sell/i)).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: /place order/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/orders' && (init as RequestInit)?.method === 'POST')
    expect(call).toBeTruthy()
    const body = JSON.parse((call![1] as RequestInit).body as string)
    expect(body).toMatchObject({
      account_id: 100, symbol: 'EURUSD', side: 'SELL',
      order_type: 'MARKET', volume_lots: 0.5,
    })
  })
})

test('limit order includes the limit price', async () => {
  setRole('trader')
  const fetchMock = mockRoutes()
  renderTrade()

  await waitFor(() => {
    expect((screen.getByLabelText(/symbol/i) as HTMLInputElement).value).toBe('EURUSD')
  })

  await userEvent.selectOptions(screen.getByLabelText(/order type/i), 'LIMIT')
  await userEvent.type(screen.getByLabelText(/limit price/i), '1.0950')
  await userEvent.clear(screen.getByLabelText(/volume/i))
  await userEvent.type(screen.getByLabelText(/volume/i), '1')
  await userEvent.click(screen.getByRole('button', { name: /review order/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /place order/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/orders' && (init as RequestInit)?.method === 'POST')
    const body = JSON.parse((call![1] as RequestInit).body as string)
    expect(body.order_type).toBe('LIMIT')
    expect(body.limit_price).toBe(1.095)
  })
})

test('shows a not-copied note when a slave account is selected', async () => {
  setRole('trader')
  mockRoutes()
  renderTrade()

  const select = await screen.findByLabelText(/account/i)
  await waitFor(() => {
    expect((screen.getByLabelText(/symbol/i) as HTMLInputElement).value).toBe('EURUSD')
  })
  await userEvent.selectOptions(select, '101')

  expect(
    await screen.findByText(/not.*copied/i)
  ).toBeInTheDocument()
})

test('lists the account open positions and closes one on confirm', async () => {
  setRole('trader')
  const fetchMock = mockRoutes()
  renderTrade()

  // Position row from /details
  expect(await screen.findByText('7001')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /^close$/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close position/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/api/orgs/1/positions/close'))
    expect(call).toBeTruthy()
    const body = JSON.parse((call![1] as RequestInit).body as string)
    expect(body).toMatchObject({ account_id: 100, position_id: 7001 })
  })
})

test('cancels a working order', async () => {
  setRole('trader')
  const fetchMock = mockRoutes()
  renderTrade()

  expect(await screen.findByText('9001')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /cancel order/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /^cancel this order$/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/api/orgs/1/orders/cancel'))
    expect(call).toBeTruthy()
    const body = JSON.parse((call![1] as RequestInit).body as string)
    expect(body).toMatchObject({ account_id: 100, order_id: 9001 })
  })
})

// ---------- Task 17: role gating ----------

test('viewer (below trade) sees a notice instead of the order ticket', async () => {
  setRole('viewer')
  mockRoutes()
  renderTrade()

  expect(await screen.findByText(/your role does not allow placing orders/i)).toBeInTheDocument()
  expect(screen.queryByLabelText(/account/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /review order/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /cancel order/i })).not.toBeInTheDocument()
  // The margin estimate lives inside the ticket, so it goes with it.
  expect(screen.queryByText(/margin required/i)).not.toBeInTheDocument()
})

test('trader (trade+) sees the full order ticket', async () => {
  setRole('trader')
  mockRoutes()
  renderTrade()

  expect(await screen.findByLabelText(/account/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /review order/i })).toBeInTheDocument()
  expect(screen.queryByText(/your role does not allow placing orders/i)).not.toBeInTheDocument()
})
