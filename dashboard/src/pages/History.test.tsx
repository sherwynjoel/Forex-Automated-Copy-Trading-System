import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import History from './History'

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
    connection_status: 'active', nickname: 'Second',
  },
]

const deals = {
  deals: [
    {
      deal_id: 1, order_id: 11, position_id: 21, symbol_id: 1, symbol: 'EURUSD',
      side: 'BUY', volume: 100000, filled_volume: 100000, volume_lots: '0.01',
      execution_price: 1.1, status: 'FILLED', commission: -0.7,
      create_timestamp: 1700000000000, execution_timestamp: 1700000000000, close: null,
    },
    {
      deal_id: 2, order_id: 12, position_id: 21, symbol_id: 1, symbol: 'EURUSD',
      side: 'SELL', volume: 100000, filled_volume: 100000, volume_lots: '0.01',
      execution_price: 1.12, status: 'FILLED', commission: -0.7,
      create_timestamp: 1700100000000, execution_timestamp: 1700100000000,
      close: {
        entry_price: 1.1, gross_profit: 20, swap: -0.12, commission: -0.7,
        balance: 10020, closed_volume: 100000, closed_volume_lots: '0.01',
      },
    },
  ],
  has_more: false,
}

const orders = {
  orders: [
    {
      order_id: 11, symbol_id: 1, symbol: 'EURUSD', side: 'BUY', volume: 100000,
      volume_lots: '0.01', order_type: 'LIMIT', status: 'FILLED',
      limit_price: 1.095, stop_price: null, execution_price: 1.095,
      executed_volume: 100000, position_id: 21, label: 'manual',
      open_timestamp: 1700000000000, update_timestamp: 1700000001000,
      stop_loss: null, take_profit: null,
    },
  ],
  has_more: false,
}

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/history/deals')) return respond(deals)
    if (url.includes('/history/orders')) return respond(orders)
    if (url.includes('/history/cashflow')) {
      return respond({ entries: [
        { id: 1, type: 'DEPOSIT', amount: 10000, balance_after: 10000,
          timestamp: 1700000000000, note: 'initial funding' },
        { id: 2, type: 'WITHDRAW', amount: 1000, balance_after: 9000,
          timestamp: 1700100000000, note: null },
      ] })
    }
    if (url.includes('/positions/21/deals')) {
      return respond({ deals: deals.deals, has_more: false })
    }
    if (url.includes('/api/accounts')) return respond(accounts)
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderHistory() {
  return render(
    <MemoryRouter>
      <History />
    </MemoryRouter>
  )
}

test('loads deal history for the master over the last week by default', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/history/deals'))
    expect(call).toBeTruthy()
    const url = String(call![0])
    expect(url).toContain('/api/accounts/100/history/deals')
    const params = new URLSearchParams(url.split('?')[1])
    const from = Number(params.get('from'))
    const to = Number(params.get('to'))
    expect(to - from).toBe(7 * 24 * 3600 * 1000)
  })
})

test('closed positions tab shows realized P&L from closing deals', async () => {
  mockRoutes()
  renderHistory()

  // Default tab: closed positions -- only the deal with a close detail.
  expect(await screen.findByText('+20.00')).toBeInTheDocument()
  expect(screen.getByText('1.10000 → 1.12000')).toBeInTheDocument()
})

test('deals tab lists every fill', async () => {
  mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  await userEvent.click(screen.getByRole('button', { name: /^deals/i }))

  // Both fills visible: the opening BUY at 1.10 and the closing SELL at 1.12
  expect(await screen.findByText('1.10000')).toBeInTheDocument()
  expect(screen.getByText('1.12000')).toBeInTheDocument()
  expect(screen.getByText('Open')).toBeInTheDocument()
  expect(screen.getByText('Close')).toBeInTheDocument()
})

test('orders tab fetches and lists order history', async () => {
  mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  await userEvent.click(screen.getByRole('button', { name: /^orders/i }))

  expect(await screen.findByText('manual')).toBeInTheDocument()
  expect(screen.getByText('LIMIT')).toBeInTheDocument()
})

test('switching account refetches its history', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  await userEvent.selectOptions(await screen.findByLabelText(/account/i), '101')

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/accounts/101/history/deals'))).toBe(true)
  })
})

test('previous week button pages the window back', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  const firstCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/history/deals'))
  const firstFrom = Number(new URLSearchParams(String(firstCall![0]).split('?')[1]).get('from'))

  await userEvent.click(screen.getByRole('button', { name: /earlier/i }))

  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(([u]) => String(u).includes('/history/deals'))
    const lastFrom = Number(
      new URLSearchParams(String(calls[calls.length - 1][0]).split('?')[1]).get('from'))
    expect(lastFrom).toBe(firstFrom - 7 * 24 * 3600 * 1000)
  })
})


test('cash flow tab lists deposits and withdrawals', async () => {
  mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  await userEvent.click(screen.getByRole('button', { name: /cash flow/i }))

  expect(await screen.findByText('DEPOSIT')).toBeInTheDocument()
  expect(screen.getByText('WITHDRAW')).toBeInTheDocument()
  expect(screen.getByText('initial funding')).toBeInTheDocument()
  expect(screen.getByText('+10,000.00')).toBeInTheDocument()
  expect(screen.getByText('-1,000.00')).toBeInTheDocument()
})

test('clicking a closed position opens its deal lifecycle', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findByText('+20.00')
  await userEvent.click(screen.getByRole('button', { name: /view position 21/i }))

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/accounts/100/positions/21/deals'))).toBe(true)
  })
  // Drawer shows both legs of the position
  expect(await screen.findByText(/position 21/i)).toBeInTheDocument()
  const drawer = screen.getByRole('complementary')
  expect(within(drawer).getByText('1.10000')).toBeInTheDocument()
  expect(within(drawer).getByText('1.12000')).toBeInTheDocument()
})