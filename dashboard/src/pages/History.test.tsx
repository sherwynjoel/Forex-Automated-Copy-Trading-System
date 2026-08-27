import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import History, { historyPacing } from './History'
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

// Production paces fleet history requests so the broker keeps answering.
// The delay is real and deliberate; tests exercise the LOGIC at zero wait.
historyPacing.gapMs = 0
historyPacing.retryMs = 0

function mockRoutes(overrides: Record<string, unknown | (() => Response)> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    for (const [fragment, payload] of Object.entries(overrides)) {
      if (url.includes(fragment)) {
        return typeof payload === 'function' ? (payload as () => Response)() : respond(payload)
      }
    }
    if (url.includes('/symbols')) {
      return respond([
        { name: 'EURUSD', symbol_id: 1, digits: 5, min_volume_lots: 0.01, step_volume_lots: 0.01 },
      ])
    }
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
    if (url.includes('/api/orgs/1/accounts')) return respond(accounts)
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/**
 * Renders History on the PER-ACCOUNT view.
 *
 * The page now opens on the whole fleet, which is what an operator wants
 * first. These tests predate that and exercise the single-account tabs, so
 * the helper navigates there rather than each test repeating the click.
 * The real default is asserted separately, by the tests at the end of this
 * file -- otherwise this helper would quietly hide a regression in it.
 */
function renderHistory() {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  const result = render(
    <MemoryRouter>
      <History />
    </MemoryRouter>
  )
  // The tablist renders immediately; only its panel waits on data.
  fireEvent.click(screen.getByRole('tab', { name: /closed positions/i }))
  return result
}

/** Renders and stays on the default view. */
function renderHistoryDefault() {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
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
    expect(url).toContain('/api/orgs/1/accounts/100/history/deals')
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
  expect(await screen.findAllByText('+20.00')).toHaveLength(2)  // row + totals
  expect(screen.getByText('1.10000 → 1.12000')).toBeInTheDocument()
})

test('deals tab lists every fill', async () => {
  mockRoutes()
  renderHistory()

  await screen.findAllByText('+20.00')
  await userEvent.click(screen.getByRole('tab', { name: /^deals/i }))

  // Both fills visible: the opening BUY at 1.10 and the closing SELL at 1.12
  expect(await screen.findByText('1.10000')).toBeInTheDocument()
  expect(screen.getByText('1.12000')).toBeInTheDocument()
  expect(screen.getByText('Open')).toBeInTheDocument()
  expect(screen.getByText('Close')).toBeInTheDocument()
})

test('orders tab fetches and lists order history', async () => {
  mockRoutes()
  renderHistory()

  await screen.findAllByText('+20.00')
  await userEvent.click(screen.getByRole('tab', { name: /^orders/i }))

  expect(await screen.findByText('manual')).toBeInTheDocument()
  expect(screen.getByText('LIMIT')).toBeInTheDocument()
})

test('switching account refetches its history', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findAllByText('+20.00')
  await userEvent.selectOptions(await screen.findByLabelText(/account/i), '101')

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/orgs/1/accounts/101/history/deals'))).toBe(true)
  })
})

test('previous week button pages the window back', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findAllByText('+20.00')
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

  await screen.findAllByText('+20.00')
  await userEvent.click(screen.getByRole('tab', { name: /cash flow/i }))

  expect(await screen.findByText('DEPOSIT')).toBeInTheDocument()
  expect(screen.getByText('WITHDRAW')).toBeInTheDocument()
  expect(screen.getByText('initial funding')).toBeInTheDocument()
  expect(screen.getByText('+10,000.00')).toBeInTheDocument()
  expect(screen.getByText('-1,000.00')).toBeInTheDocument()
})

test('clicking a closed position opens its deal lifecycle', async () => {
  const fetchMock = mockRoutes()
  renderHistory()

  await screen.findAllByText('+20.00')
  await userEvent.click(screen.getByRole('button', { name: /view position 21/i }))

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/api/orgs/1/accounts/100/positions/21/deals'))).toBe(true)
  })
  // Drawer shows both legs of the position
  expect(await screen.findByText(/position 21/i)).toBeInTheDocument()
  const drawer = screen.getByRole('dialog')
  expect(within(drawer).getByText('1.10000')).toBeInTheDocument()
  expect(within(drawer).getByText('1.12000')).toBeInTheDocument()
})
test('a failed history fetch shows a friendly error with retry, never a fake empty state', async () => {
  let fail = true
  mockRoutes({
    '/history/deals': () => fail
      ? new Response(JSON.stringify({ detail: 'deal history failed: INVALID_REQUEST' }), { status: 400 })
      : new Response(JSON.stringify(deals), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  })
  renderHistory()

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent(/deal history failed/i)
  // errorText() strips the status-code prefix; no raw "400:" reaches the user.
  expect(alert).not.toHaveTextContent('400:')
  // An error must never coexist with a confident "no trades" claim.
  expect(screen.queryByText(/no positions were closed/i)).not.toBeInTheDocument()

  fail = false
  await userEvent.click(screen.getByRole('button', { name: /retry/i }))
  expect(await screen.findByText('21')).toBeInTheDocument()
})

test('closed positions get a net column and a week-totals row', async () => {
  mockRoutes()
  renderHistory()

  await screen.findByText('21')
  expect(screen.getByText('Net')).toBeInTheDocument()
  // net = gross 20 + swap -0.12 + commission -0.7 = 19.18, in the row AND the totals row
  expect(screen.getAllByText('+19.18')).toHaveLength(2)
  expect(screen.getByText(/week total/i)).toBeInTheDocument()
})

test('a slave copy links back to its master position', async () => {
  const fetchMock = mockRoutes({
    '/accounts/101/history/orders': {
      orders: [{ ...orders.orders[0], label: 'copy:m42', position_id: 21 }],
      has_more: false,
    },
  })
  renderHistory()
  await screen.findByText('21')

  await userEvent.selectOptions(screen.getByLabelText(/account/i), '101')
  const chip = await screen.findByRole('button', { name: /copy of #42/i })
  await userEvent.click(chip)

  // The drill fetch goes to the MASTER account (100), not the selected slave.
  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([u]) =>
      String(u).includes('/accounts/100/positions/42/deals'))).toBe(true)
  })
  expect(await screen.findByText(/position 42/i)).toBeInTheDocument()
})

test('Later is disabled on the current window and enables after paging back', async () => {
  mockRoutes()
  renderHistory()
  await screen.findByText('21')

  const later = screen.getByRole('button', { name: /later/i })
  expect(later).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: /earlier/i }))
  expect(later).toBeEnabled()
})

test('refresh refetches and stamps the fetch time', async () => {
  const fetchMock = mockRoutes()
  renderHistory()
  await screen.findByText('21')

  const countDeals = () =>
    fetchMock.mock.calls.filter(([u]) => String(u).includes('/history/deals')).length
  const before = countDeals()
  await userEvent.click(screen.getByRole('button', { name: /refresh/i }))
  await waitFor(() => {
    expect(countDeals()).toBeGreaterThan(before)
  })
  expect(screen.getByText(/as of/i)).toBeInTheDocument()
})

test('Escape closes the drill drawer', async () => {
  mockRoutes()
  renderHistory()
  await screen.findByText('21')

  await userEvent.click(screen.getByRole('button', { name: /view position 21/i }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('day view narrows the window and the truncation note is actionable', async () => {
  const fetchMock = mockRoutes({
    '/history/deals': { ...deals, has_more: true },
  })
  renderHistory()
  await screen.findByText('21')

  // The old copy told users to "narrow the range" with no control to do so.
  expect(screen.getByText(/switch to day view/i)).toBeInTheDocument()
  expect(screen.queryByText(/narrow the range/i)).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /^day$/i }))
  await waitFor(() => {
    const last = fetchMock.mock.calls.filter(([u]) => String(u).includes('/history/deals')).pop()
    const url = new URL(String(last![0]), 'http://x')
    const from = Number(url.searchParams.get('from'))
    const to = Number(url.searchParams.get('to'))
    expect(to - from).toBe(24 * 3600 * 1000)
  })
  // The totals caption follows the window size.
  expect(await screen.findByText(/day total/i)).toBeInTheDocument()
  expect(screen.queryByText(/week total/i)).not.toBeInTheDocument()
})

test('the date picker jumps the window to that day', async () => {
  const fetchMock = mockRoutes()
  renderHistory()
  await screen.findByText('21')

  fireEvent.change(screen.getByLabelText(/jump to date/i), { target: { value: '2026-08-01' } })
  await waitFor(() => {
    const last = fetchMock.mock.calls.filter(([u]) => String(u).includes('/history/deals')).pop()
    const url = new URL(String(last![0]), 'http://x')
    const to = Number(url.searchParams.get('to'))
    expect(to).toBe(new Date(2026, 7, 2).getTime())
  })
})

test('prices render with the symbol own digits, not a hardcoded five', async () => {
  mockRoutes({
    '/symbols': [{ name: 'EURUSD', symbol_id: 1, digits: 3, min_volume_lots: 0.01, step_volume_lots: 0.01 }],
  })
  renderHistory()

  await screen.findByText('21')
  expect(screen.getByText('1.100 → 1.120')).toBeInTheDocument()
  expect(screen.queryByText('1.10000 → 1.12000')).not.toBeInTheDocument()
})

test('the account label does not repeat the login when there is no nickname', async () => {
  mockRoutes()
  renderHistory()
  await screen.findByText('21')

  expect(screen.getByRole('option', { name: 'Account 90100 · Demo' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: /90100 · 90100/ })).not.toBeInTheDocument()
})

test('retry recovers when the accounts list itself failed to load', async () => {
  let failAccounts = true
  const inner = mockRoutes()
  // Only the accounts LIST fails; history URLs also contain "/accounts/" so
  // an includes-override would poison them too.
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).split('?')[0]
    if (failAccounts && /\/api\/orgs\/1\/accounts$/.test(url)) {
      return new Response(JSON.stringify({ detail: 'accounts unavailable' }), { status: 502 })
    }
    return inner(input, init)
  }))
  renderHistory()

  await screen.findByRole('alert')
  failAccounts = false
  await userEvent.click(screen.getByRole('button', { name: /retry/i }))
  // Retry reloads the ACCOUNTS list, then history follows.
  expect(await screen.findAllByText('+20.00')).toHaveLength(2)
})

test('a refresh failure keeps the rows that were already on screen', async () => {
  let fail = false
  mockRoutes({
    '/history/deals': () => fail
      ? new Response(JSON.stringify({ detail: 'broker hiccup' }), { status: 502 })
      : new Response(JSON.stringify(deals), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  })
  renderHistory()
  await screen.findAllByText('+20.00')

  fail = true
  await userEvent.click(screen.getByRole('button', { name: /refresh/i }))

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent(/broker hiccup/i)
  // The last good rows stay visible under the error instead of vanishing.
  expect(screen.getAllByText('+20.00')).toHaveLength(2)
})

test('tabs move with arrow keys', async () => {
  mockRoutes()
  renderHistory()
  await screen.findAllByText('+20.00')

  const closedTab = screen.getByRole('tab', { name: /closed positions/i })
  closedTab.focus()
  await userEvent.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab', { name: /all accounts/i })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: /all accounts/i })).toHaveFocus()
})

test('closing the drill drawer returns focus to the position that opened it', async () => {
  mockRoutes()
  renderHistory()
  await screen.findAllByText('+20.00')

  const opener = screen.getByRole('button', { name: /view position 21/i })
  await userEvent.click(opener)
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
})

const slaveDeals = {
  deals: [
    {
      deal_id: 51, order_id: 99, position_id: 31, symbol_id: 1, symbol: 'EURUSD',
      side: 'SELL', volume: 100000, filled_volume: 100000, volume_lots: '0.01',
      execution_price: 1.1195, status: 'FILLED', commission: -0.6,
      create_timestamp: 1700100000500, execution_timestamp: 1700100000500,
      close: {
        entry_price: 1.1005, gross_profit: 19, swap: 0, commission: -0.6,
        balance: 5019, closed_volume: 100000, closed_volume_lots: '0.01',
      },
    },
  ],
  has_more: false,
}

test('the By-master tab nests slave copies under their master trade', async () => {
  const fetchMock = mockRoutes({
    '/accounts/101/history/deals': slaveDeals,
    '/accounts/101/history/orders': {
      orders: [{ ...orders.orders[0], order_id: 99, position_id: 31, label: 'copy:m21' }],
      has_more: false,
    },
  })
  renderHistory()
  await screen.findAllByText('+20.00')

  await userEvent.click(screen.getByRole('tab', { name: /all accounts/i }))

  // Master group header: position #21 with its aggregate net (20 - 0.12 - 0.7).
  expect(await screen.findByText('#21')).toBeInTheDocument()
  expect(screen.getAllByText('+19.18').length).toBeGreaterThan(0)
  // The slave's copied execution sits beneath it, named and drillable.
  const panel = screen.getByRole('tabpanel')
  expect(await within(panel).findByText(/Second · 90101/)).toBeInTheDocument()
  expect(within(panel).getByText('31')).toBeInTheDocument()
  // The fleet load fetched the SLAVE account's history too.
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).includes('/accounts/101/history/deals'))).toBe(true)
})

test('a master trade with no copies in the window says so', async () => {
  mockRoutes({
    '/accounts/101/history/deals': { deals: [], has_more: false },
    '/accounts/101/history/orders': { orders: [], has_more: false },
  })
  renderHistory()
  await screen.findAllByText('+20.00')

  await userEvent.click(screen.getByRole('tab', { name: /all accounts/i }))
  expect(await screen.findByText('#21')).toBeInTheDocument()
  expect(screen.getByText(/no slave copies in this week/i)).toBeInTheDocument()
})

test('By-master survives one slave failing and says which copies are missing', async () => {
  mockRoutes({
    '/accounts/101/history/deals': () =>
      new Response(JSON.stringify({ detail: 'BLOCKED_PAYLOAD_TYPE' }), { status: 502 }),
  })
  renderHistory()
  await screen.findAllByText('+20.00')

  await userEvent.click(screen.getByRole('tab', { name: /all accounts/i }))

  // The master's groups still render...
  expect(await screen.findByText('#21')).toBeInTheDocument()
  // ...with an honest warning about the failed slave, and a way to retry.
  expect(screen.getByText(/could not load/i)).toHaveTextContent(/Second · 90101/)
  expect(screen.getByRole('button', { name: /retry fleet/i })).toBeInTheDocument()
})

test('By-master shows an error, never a fake empty, when the master load fails', async () => {
  let masterDealCalls = 0
  const inner = mockRoutes()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/accounts/100/history/deals')) {
      masterDealCalls += 1
      // First call = the per-account tab load (succeeds); later fleet calls fail.
      if (masterDealCalls > 1) {
        return new Response(JSON.stringify({ detail: 'BLOCKED_PAYLOAD_TYPE' }), { status: 502 })
      }
    }
    return inner(input, init)
  }))
  renderHistory()
  await screen.findAllByText('+20.00')

  await userEvent.click(screen.getByRole('tab', { name: /all accounts/i }))

  expect(await screen.findByText(/master account's history could not be loaded/i)).toBeInTheDocument()
  // The broker's rate-limit enum is explained, not left raw and unexplained.
  expect(screen.getByText(/rate-limits history/i)).toBeInTheDocument()
  expect(screen.queryByText(/no master positions were closed/i)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /retry fleet/i })).toBeInTheDocument()
})


test('a throttled account is retried, not silently dropped from the fleet view', async () => {
  // cTrader refuses a whole-fleet burst of history requests. Measured on
  // production: fired back-to-back, the first five answered and the next
  // three came back 400, so a ten-account fleet showed roughly the first
  // three accounts and the rest looked like they had no history at all.
  // One refusal must cost a retry, never the account.
  let firstCall = true
  const fetchMock = mockRoutes({
    '/accounts/101/history/deals': () => {
      if (firstCall) {
        firstCall = false
        return new Response('BLOCKED_PAYLOAD_TYPE', { status: 400 })
      }
      return new Response(JSON.stringify(deals), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    },
  })
  renderHistory()

  await userEvent.click(await screen.findByRole('tab', { name: /all accounts/i }))

  // The retry must actually happen: the same URL is requested twice.
  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).includes('/accounts/101/history/deals'))
    expect(calls.length).toBeGreaterThanOrEqual(2)
  }, { timeout: 10000 })

  // And the account must NOT be listed as failed once the retry succeeds.
  await waitFor(() => {
    expect(screen.queryByText(/could not load/i)).not.toBeInTheDocument()
  }, { timeout: 10000 })
}, 20000)


test('History opens on the whole fleet, not one account', async () => {
  // The question this page answers is "did every slave copy the master?".
  // Opening on a single account buried that behind a tab an operator had
  // to know existed.
  mockRoutes()
  renderHistoryDefault()

  const fleetTab = await screen.findByRole('tab', { name: /all accounts/i })
  expect(fleetTab).toHaveAttribute('aria-selected', 'true')
})

test('the account picker is hidden on the fleet view, shown on a per-account tab', async () => {
  // Leaving it visible invites the operator to pick an account and watch
  // nothing happen, because the fleet view covers all of them.
  mockRoutes()
  renderHistoryDefault()

  await screen.findByRole('tab', { name: /all accounts/i })
  expect(screen.queryByLabelText('Account')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('tab', { name: /closed positions/i }))
  expect(await screen.findByLabelText('Account')).toBeInTheDocument()
})

test('the fleet view does not also burst-fetch the selected account', async () => {
  // loadHistory fires three parallel requests. Running it alongside the
  // paced whole-fleet load competes for the same broker throttle, which is
  // exactly what drops accounts out of the view.
  const fetchMock = mockRoutes()
  renderHistoryDefault()

  await screen.findByRole('tab', { name: /all accounts/i })
  await waitFor(() => {
    expect(fetchMock.mock.calls.some(
      (c) => String(c[0]).includes('/history/cashflow'))).toBe(false)
  })
})


test('the default view renders instead of sitting on "Loading history"', async () => {
  // Regression: the outer gate waited on the SINGLE-ACCOUNT load, which the
  // fleet tab deliberately does not run. `loading` stayed true forever, so
  // the page rendered nothing at all -- a permanently loading History for
  // every operator who simply opened it.
  mockRoutes()
  renderHistoryDefault()

  // The fleet view must reach a rendered state, not the outer spinner.
  await waitFor(() => {
    expect(screen.queryByText('Loading history…')).not.toBeInTheDocument()
  }, { timeout: 10000 })

  expect(await screen.findByRole('tabpanel')).toBeInTheDocument()
}, 20000)


test('a copy shows how far its EXIT sat from the master, not just its entry', async () => {
  // Entering together says nothing about leaving together. A copy can open
  // at the master's price and still exit somewhere else -- its own stop, a
  // later fill, a wider spread on the way out -- and that gap is just as
  // much of the difference in what the slave actually earned.
  mockRoutes({
    '/accounts/101/history/deals': slaveDeals,
    '/accounts/101/history/orders': {
      orders: [{ ...orders.orders[0], order_id: 99, position_id: 31, label: 'copy:m21' }],
      has_more: false,
    },
  })
  renderHistory()
  await screen.findAllByText('+20.00')

  await userEvent.click(screen.getByRole('tab', { name: /all accounts/i }))
  const panel = await screen.findByRole('tabpanel')
  await within(panel).findByText(/Second · 90101/)

  // Master 1.10000 -> 1.12000; copy 1.10050 -> 1.11950.
  expect(within(panel).getByText('+0.00050')).toBeInTheDocument()   // entry
  expect(within(panel).getByText('-0.00050')).toBeInTheDocument()   // exit
})
