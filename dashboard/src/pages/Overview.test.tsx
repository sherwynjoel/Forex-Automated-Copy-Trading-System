import { render, screen, waitFor, fireEvent, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Overview from './Overview'
import type { Account, ApiState, Settings, StateSnapshot } from '../lib/types'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'
import { mt5Account } from '../test/mt5Fixtures'
import { publishSettings } from '../lib/settingsBus'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))
const { fakeSockets } = vi.hoisted(() => ({
  fakeSockets: [] as Array<{ onmessage: ((e: { data: string }) => void) | null }>,
}))
vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    eventsSocket: () => {
      const ws = { onmessage: null, onclose: null, onerror: null, close: () => {} }
      fakeSockets.push(ws as never)
      return ws as unknown as WebSocket
    },
  }
})

function setRole(role: Role) {
  useOrgMock.mockReturnValue(mockUseOrg(role))
}

// Helper to stub API routes
function stubApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn((path: string) => {
    const response = routes[path]
    if (response === undefined) {
      return Promise.resolve(new Response(null, { status: 404 }))
    }
    if (response instanceof Response) {
      return Promise.resolve(response)
    }
    return Promise.resolve(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const mockAccounts: Account[] = [
  {
    ctid_trader_account_id: 1,
    trader_login: 1001,
    is_live: true,
    role: 'master',
    enabled: true,
    multiplier: 1,
    status: 'connected',
    connection_status: 'ok',
  },
  {
    ctid_trader_account_id: 2,
    trader_login: 1002,
    is_live: false,
    role: 'slave',
    enabled: true,
    multiplier: 1,
    status: 'connected',
    connection_status: 'ok',
  },
  {
    ctid_trader_account_id: 3,
    trader_login: 1003,
    is_live: false,
    role: 'slave',
    enabled: false,
    multiplier: 1,
    status: 'disconnected',
    connection_status: 'degraded',
  },
]

const mockSettings: Settings = {
  copying_enabled: true,
  dry_run: false,
}

// The per-account block of GET /api/orgs/1/state. Kept separate so tests can
// assert against the same numbers the component is expected to render.
const mockAccountState: StateSnapshot = {
  '1': {
    balance: 10000,
    equity: 12000,
    open_pnl: 2000,
    positions: [
      {
        position_id: 101,
        symbol_id: 1,
        symbol: 'EURUSD',
        side: 'BUY',
        volume: 1.0,
        entry_price: 1.0800,
        pnl_quote: 200,
        current_price: 2381.5,
      },
    ],
  },
  '2': {
    balance: 5000,
    equity: 5500,
    open_pnl: 500,
    positions: [
      {
        position_id: 102,
        symbol_id: 1,
        symbol: 'EURUSD',
        side: 'BUY',
        volume: 1.0,
        entry_price: 1.0800,
        pnl_quote: 500,
      },
    ],
  },
  '3': {
    balance: 3000,
    equity: 2900,
    open_pnl: -100,
    positions: [],
  },
}

// What GET /api/orgs/1/state ACTUALLY returns: a verbatim pass-through of the
// copier's get_state(), i.e. an envelope whose per-account block sits under
// `accounts`, alongside master_positions/pending_orders/drift.
//
// This mock used to be a bare StateSnapshot -- the account map on its own --
// which is exactly the shape Overview wrongly assumed. The mock agreeing with
// the bug is why the suite passed while the master card and every slave tile's
// equity/balance rendered blank against the real API. Mocking the real
// envelope is what makes this suite able to catch that class of bug at all.
const mockState: ApiState = {
  accounts: mockAccountState,
  master_positions: [],
  pending_orders: [],
  drift: [],
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  publishSettings(null)
})

test('master equity sits in the KPI row; the master card keeps balance and P&L', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  // Wait for master card to render (check for master account header)
  await waitFor(() => {
    expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
  })

  // Check master card displays the values
  expect(screen.getByText('12,000.00')).toBeInTheDocument()
  expect(screen.getByText('10,000.00')).toBeInTheDocument()
  expect(screen.getByText('+2,000.00')).toBeInTheDocument()
})

test('master card renders equity/balance/P&L read from the nested accounts block', async () => {
  setRole('admin')
  // Regression guard for the shape bug: /api/orgs/1/state returns
  // {accounts, master_positions, pending_orders, drift}, and the per-account
  // numbers live under `accounts`. Overview used to index the envelope
  // directly, so every lookup was undefined -- the master card did not render
  // at all and the slave tiles' stats were silently missing. The old mock was
  // a bare account map, so the suite never noticed.
  //
  // This test asserts against an envelope that carries a NON-empty
  // master_positions/drift too, so a future "just spread the response" fix
  // that reintroduces the flat read cannot pass by accident.
  const envelope: ApiState = {
    accounts: mockAccountState,
    master_positions: [
      {
        position_id: 9001,
        symbol_id: 1,
        symbol: 'EURUSD',
        side: 'BUY',
        volume: 10_000_000,
        price: 1.105,
        label: 'copy:m9001',
        pnl_quote: 12.5,
        volume_lots: '1.00',
        copies: [],
      },
    ],
    pending_orders: [],
    drift: [],
  }

  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': envelope,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  // The master card only renders when the account's state block was found.
  await waitFor(() => {
    expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
  })

  // Account 1 is the master; its numbers come from envelope.accounts['1'].
  expect(screen.getByText('12,000.00')).toBeInTheDocument() // equity
  expect(screen.getByText('10,000.00')).toBeInTheDocument() // balance
  expect(screen.getByText('+2,000.00')).toBeInTheDocument() // open P&L

  // ...and a slave tile's stats come from the same nested block, so a
  // regression cannot hide behind the master card alone.
  expect(screen.getByText('5,500.00')).toBeInTheDocument() // slave 2 equity
  expect(screen.getByText('5,000.00')).toBeInTheDocument() // slave 2 balance
})

test('renders no account stats when the accounts block is empty, without crashing', async () => {
  setRole('admin')
  // The honest empty case: the copier returns `accounts: {}` before the state
  // tracker has any data (e.g. no master configured). The screen must degrade
  // to "no stats" rather than throwing on an undefined lookup.
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': { accounts: {}, master_positions: [], pending_orders: [], drift: [] },
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')

  expect(screen.queryByText(/Master account \(1001\)/)).not.toBeInTheDocument()
  expect(screen.queryByText('12,000.00')).not.toBeInTheDocument()
  // Slave tiles still render (they come from /api/orgs/1/accounts), just without stats.
  expect(screen.getAllByTestId('slave-tile').length).toBeGreaterThanOrEqual(2)
})

test('renders slave tiles with status icons', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  // Wait for data to load
  await screen.findAllByTestId('slave-tile')

  // Check slave tiles are rendered with login numbers. Each tile's View link
  // also names its account for screen readers, so the login is matched on
  // the tile heading rather than on any text.
  expect(screen.getByRole('heading', { name: 'Account 1002' })).toBeInTheDocument() // Follower 1 (ok)
  expect(screen.getByRole('heading', { name: 'Account 1003' })).toBeInTheDocument() // Follower 2 (degraded)

  // Check status icons exist (use emoji checks or data-testid)
  const tiles = screen.getAllByTestId(/slave-tile/)
  expect(tiles.length).toBeGreaterThanOrEqual(2)
})

test('per-slave pause posts to /api/orgs/1/control/pause with account_id', async () => {
  setRole('admin')
  const routes: Record<string, unknown> = {
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  }

  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    // Handle pause/resume endpoints
    if (path.includes('/api/orgs/1/control/pause') || path.includes('/api/orgs/1/control/resume')) {
      return Promise.resolve(new Response(null, { status: 204 }))
    }

    const response = routes[path]
    if (response === undefined) {
      return Promise.resolve(new Response(null, { status: 404 }))
    }
    if (response instanceof Response) {
      return Promise.resolve(response)
    }
    return Promise.resolve(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
  })
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.queryAllByRole('button', { name: /pause/i }).length).toBeGreaterThan(0)
  })

  const pauseButtons = screen.getAllByRole('button', { name: /pause/i })
  await userEvent.click(pauseButtons[0])

  await waitFor(() => {
    const pauseCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && call[0].includes('/api/orgs/1/control/pause')
    )
    expect(pauseCall).toBeDefined()
    const body = JSON.parse(pauseCall![1]?.body as string)
    expect(body.account_id).toBe(2)
  })
})

test('shows dry-run badge when dry_run enabled', async () => {
  setRole('admin')
  const dryRunSettings: Settings = {
    copying_enabled: true,
    dry_run: true,
  }

  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': dryRunSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/DRY RUN/i)).toBeInTheDocument()
  })
  // The one place Overview states it: the Attention card's calm line.
  expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — dry run, copies are simulated')
})

test('does not show refresh-failed banner when all connections are active', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
  })

  expect(screen.queryByTestId('refresh-failed-banner')).not.toBeInTheDocument()
})

test('shows prominent refresh-failed banner naming the affected account', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'refresh_failed' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByTestId('refresh-failed-banner')).toBeInTheDocument()
  })

  const banner = screen.getByTestId('refresh-failed-banner')
  // Names the affected account by its trader login
  expect(banner).toHaveTextContent('1002')
  // Explains the operator-facing consequence and remediation
  expect(banner).toHaveTextContent(/token refresh failed/i)
  expect(banner).toHaveTextContent(/copying .* will stop/i)
  expect(banner).toHaveTextContent(/reconnect/i)
  expect(banner).toHaveTextContent(/connect ctrader id/i)
})

test('shows refresh-failed banner above the master card when the master itself has a failed refresh', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'refresh_failed' },
    { ...mockAccounts[1], connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByTestId('refresh-failed-banner')).toBeInTheDocument()
  })

  const banner = screen.getByTestId('refresh-failed-banner')
  expect(banner).toHaveTextContent('1001')

  // Banner must precede the master card in document order
  const masterHeading = screen.getByText(/Master account \(1001\)/)
  // eslint-disable-next-line no-bitwise
  expect(banner.compareDocumentPosition(masterHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('slave tile shows Degraded status from account.status, not connection_status', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], status: 'degraded', connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findAllByTestId('slave-tile')

  // account.status === 'degraded' must drive the degraded badge, even though
  // connection_status is 'active' (connection_status never holds 'degraded').
  expect(screen.getByText('Degraded')).toBeInTheDocument()
})

test('degraded slave tile shows the last_error reason', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    {
      ...mockAccounts[1],
      status: 'degraded',
      connection_status: 'active',
      last_error: 'Send failed: insufficient margin on EURUSD',
    },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByTestId('slave-last-error')).toBeInTheDocument()
  })

  const errorEl = screen.getByTestId('slave-last-error')
  expect(errorEl).toHaveTextContent('Send failed: insufficient margin on EURUSD')
  expect(errorEl).toHaveAttribute('title', 'Send failed: insufficient margin on EURUSD')

  // Too long for one line: a visible toggle expands it in place, so the
  // reason is not reachable only by hovering a tooltip.
  const toggle = within(screen.getByTestId('slave-tile-2')).getByRole('button', { name: 'Show details' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(toggle)
  expect(within(screen.getByTestId('slave-tile-2')).getByRole('button', { name: 'Hide details' }))
    .toHaveAttribute('aria-expanded', 'true')
})

test('non-degraded slave tile never shows a last_error message', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    {
      ...mockAccounts[1],
      status: 'ok',
      connection_status: 'active',
      last_error: 'Send failed: insufficient margin on EURUSD',
    },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByRole('heading', { name: 'Account 1002' })

  expect(screen.queryByTestId('slave-last-error')).not.toBeInTheDocument()
})

test('slave tile shows a refresh-failed marker distinct from degraded styling', async () => {
  setRole('admin')
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'refresh_failed' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByTestId('slave-refresh-failed-marker')).toBeInTheDocument()
  })

  // Distinct from the degraded marker - degraded label must not appear
  expect(screen.queryByText('Degraded')).not.toBeInTheDocument()
})

test(
  'polls /api/orgs/1/state every 5 seconds',
  async () => {
    setRole('admin')
    // Spy on setInterval to verify it's called with 5000ms
    const setIntervalSpy = vi.spyOn(global, 'setInterval')

    const fetchMock = stubApi({
      '/api/orgs/1/accounts': mockAccounts,
      '/api/orgs/1/settings': mockSettings,
      '/api/orgs/1/state': mockState,
    })

    render(
      <MemoryRouter>
        <Overview />
      </MemoryRouter>
    )

    // Wait for initial render and data load
    await waitFor(() => {
      expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
    })

    // Verify that /api/orgs/1/state was called during initial load
    const initialStateCalls = fetchMock.mock.calls.filter((call) => call[0] === '/api/orgs/1/state').length
    expect(initialStateCalls).toBeGreaterThan(0)

    // Verify that setInterval was called with 5000ms interval for polling
    const pollingInterval = setIntervalSpy.mock.calls.find(
      (call) => typeof call[1] === 'number' && call[1] === 5000
    )
    expect(pollingInterval).toBeDefined()

    setIntervalSpy.mockRestore()
  }
)

// ---------- the kill switch lives in the desk strip, not on Overview ----------
// Its stop/resume and dry-run behaviour is tested in components/Layout.test.tsx.

test('Overview renders no kill switch for an admin: the desk strip carries it', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
  expect(screen.queryByText('Copying Status')).not.toBeInTheDocument()
})

test('Overview renders no kill switch for a viewer either', async () => {
  setRole('viewer')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

// ---------- redesigned stat sections (org-scoped) ----------

const overviewStats = {
  accounts_connected: 3,
  masters: 1,
  active_slaves: 2,
  disabled_or_paused: 0,
  degraded: 0,
  copied_today: 5,
  yesterday: {
    total_balance: 20000,
    total_equity: 20000,
    // mockState has accounts 1, 2 and 3; give each a yesterday to compare.
    equity_by_account: { '1': 10000, '2': 5000, '3': 5000 },
  },
  recent_copies: [
    {
      status: 'active', master_position_id: 42, master_order_id: null,
      slave_account_id: 2, slave_login: 1002, slave_nickname: null,
      symbol: 'EURUSD', slave_volume: 50000, fill_price: 1.105,
      error: null, updated_at: '2026-08-18T10:00:00+00:00',
    },
    {
      status: 'failed', master_position_id: 43, master_order_id: null,
      slave_account_id: 2, slave_login: 1002, slave_nickname: null,
      symbol: 'GBPUSD', slave_volume: null, fill_price: null,
      error: 'MARKET_CLOSED', updated_at: '2026-08-18T09:00:00+00:00',
    },
  ],
}

const copierAnalytics = {
  closed_trades: 10, wins: 7, losses: 3, win_rate: 0.7,
  profit_factor: 2.5, best_trade: 300.0, worst_trade: -120.0,
  avg_win: 100, avg_loss: -50, net_pnl: 512.5,
  gross_wins: 700, gross_losses: -280, max_drawdown: 150, max_drawdown_pct: 0.01,
  equity_curve: [], per_symbol: [], weekly: [], weeks: 4, truncated: false,
}

const stateWithMasterPosition = {
  ...mockState,
  master_positions: [
    {
      position_id: 42, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
      volume: 100000, volume_lots: '0.01', price: 1.1, label: '',
      pnl_quote: 40.0, copies: [],
    },
  ],
}

/** Every read this screen makes, under THIS org's prefix. */
function statsRoutes(state: unknown = mockState) {
  return {
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': state,
    '/api/orgs/1/overview': overviewStats,
    '/api/orgs/1/accounts/1/analytics?weeks=4': copierAnalytics,
  }
}

test('portfolio row aggregates equity and compares to yesterday', async () => {
  setRole('admin')
  stubApi(statsRoutes(stateWithMasterPosition))

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  // Portfolio value = sum of every account equity in mockState
  const totalEquity = Object.values(mockState.accounts as Record<string, { equity: number | null }>)
    .reduce((a, s) => a + (s.equity ?? 0), 0)
  const formatted = totalEquity.toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  expect(await screen.findByText(formatted)).toBeInTheDocument()
  // vs yesterday: (total - 20000) / 20000, shown as a signed percentage
  expect(screen.getByText(/% vs yesterday/)).toBeInTheDocument()
  expect(screen.getByText(/3 accounts connected/)).toBeInTheDocument()
})

test("the Master equity tile's View control expands a per-account breakdown in place", async () => {
  setRole('admin')
  stubApi(statsRoutes())
  const { within } = await import('@testing-library/react')

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  expect(screen.queryByText(/portfolio breakdown/i)).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /view all accounts/i }))
  const panel = screen.getByText(/portfolio breakdown/i).closest('section')!
  // One row per account with its equity from the live state
  expect(within(panel).getByText('12,000.00')).toBeInTheDocument()
  expect(within(panel).getByText('5,500.00')).toBeInTheDocument()
  expect(within(panel).getByText('2,900.00')).toBeInTheDocument()
})

test('the Followers copying tile expands a fleet status list, accordion-style', async () => {
  setRole('admin')
  stubApi(statsRoutes())
  const { within } = await import('@testing-library/react')

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  // Open portfolio first, then accounts — accordion closes the first panel
  await userEvent.click(screen.getByRole('button', { name: /view all accounts/i }))
  expect(screen.getByText(/portfolio breakdown/i)).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /view fleet health/i }))
  expect(screen.queryByText(/portfolio breakdown/i)).not.toBeInTheDocument()
  const panel = screen.getByText(/fleet status/i).closest('section')!
  expect(within(panel).getAllByText(/follower/i).length).toBeGreaterThanOrEqual(2)
  expect(within(panel).getByText(/master/i)).toBeInTheDocument()
  expect(within(panel).getByText(/degraded/i)).toBeInTheDocument()
})

test('each open contract row can be closed through a confirm dialog', async () => {
  setRole('admin')
  const routes = statsRoutes()
  routes['/api/orgs/1/positions/close'] = { status: 'submitted' }
  const fetchMock = stubApi(routes)
  const { within } = await import('@testing-library/react')

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  await userEvent.click(screen.getByRole('button', { name: /view open positions/i }))
  const panel = screen.getByText(/open contracts/i).closest('section')!

  const closeButtons = within(panel).getAllByRole('button', { name: /^close$/i })
  expect(closeButtons.length).toBe(2) // one per contract row

  await userEvent.click(closeButtons[0])
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close position/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/positions/close'))
    expect(call).toBeTruthy()
    const body = JSON.parse((call![1] as RequestInit).body as string)
    expect(body).toMatchObject({ account_id: 1, position_id: 101 })
  })
})

test('the panel close-all button closes every listed contract after confirm', async () => {
  setRole('admin')
  const routes = statsRoutes()
  routes['/api/orgs/1/positions/close'] = { status: 'submitted' }
  const fetchMock = stubApi(routes)
  const { within } = await import('@testing-library/react')

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  await userEvent.click(screen.getByRole('button', { name: /view open positions/i }))
  const panel = screen.getByText(/open contracts/i).closest('section')!

  await userEvent.click(within(panel).getByRole('button', { name: /close all shown/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close 2 contracts/i }))

  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(([u]) => String(u).includes('/positions/close'))
    expect(calls.length).toBe(2)
    const bodies = calls.map(([, init]) => JSON.parse((init as RequestInit).body as string))
    expect(bodies).toEqual(expect.arrayContaining([
      expect.objectContaining({ account_id: 1, position_id: 101 }),
      expect.objectContaining({ account_id: 2, position_id: 102 }),
    ]))
  })
})

test("the Today's P&L tile expands the list of today's copy fills", async () => {
  // Renamed from "Copied today": the tile now leads with the fleet's P&L,
  // but it still opens the same fills panel -- that behaviour is what this
  // test protects, not the label.
  setRole('admin')
  // Pinned inside today regardless of the wall clock (an hour-ago stamp
  // crosses midnight when the suite runs just after 00:00).
  const todayStamp = new Date(); todayStamp.setHours(0, 5, 0, 0)
  const routes = statsRoutes()
  routes['/api/orgs/1/overview'] = {
    ...overviewStats,
    recent_copies: [
      {
        status: 'active', master_position_id: 42, master_order_id: null,
        slave_account_id: 2, slave_login: 1002, slave_nickname: null,
        symbol: 'EURUSD', slave_volume: 50000, fill_price: 1.105,
        error: null, updated_at: todayStamp.toISOString(),
      },
      {
        status: 'closed', master_position_id: 41, master_order_id: null,
        slave_account_id: 3, slave_login: 1003, slave_nickname: null,
        symbol: 'GBPUSD', slave_volume: 50000, fill_price: 1.27,
        error: null, updated_at: new Date(Date.now() - 3 * 86400000).toISOString(),
      },
    ],
  }
  stubApi(routes)
  const { within } = await import('@testing-library/react')

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  await userEvent.click(screen.getByRole('button', { name: /view today's fills/i }))

  const panel = screen.getByText(/today's copy fills/i).closest('section')!
  // The fill from an hour ago is listed; the three-day-old one is not
  expect(within(panel).getByText('1.105')).toBeInTheDocument()
  expect(within(panel).queryByText('1.27')).not.toBeInTheDocument()
})

test('the Open positions tile expands a live open-contracts panel in place', async () => {
  setRole('admin')
  stubApi(statsRoutes())

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  // Panel hidden until the tile is toggled
  expect(screen.queryByText('+200.00')).not.toBeInTheDocument()

  const tile = screen.getByRole('button', { name: /view open positions/i })
  expect(tile).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(tile)

  // Every running contract across accounts, with live P&L from the state feed
  expect(tile).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getAllByText('EURUSD').length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('+200.00')).toBeInTheDocument() // master's position
  expect(screen.getByText('2381.5')).toBeInTheDocument() // its live marking price
  expect(screen.getByText('+500.00')).toBeInTheDocument() // slave's position

  // Toggles closed again
  await userEvent.click(tile)
  expect(screen.queryByText('+200.00')).not.toBeInTheDocument()
})

test('copy log lists copies with estimated P&L and failure reasons', async () => {
  setRole('admin')
  stubApi(statsRoutes(stateWithMasterPosition))

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  expect(await screen.findByText('Copy log')).toBeInTheDocument()
  expect(screen.getByText('EURUSD')).toBeInTheDocument()
  // Active copy P&L estimate: master pnl 40 * (slave 50000 / master 100000) = +20.00
  expect(screen.getByText('+20.00')).toBeInTheDocument()
  // Failed copy shows the broker reason instead of a number
  expect(screen.getByText('MARKET_CLOSED')).toBeInTheDocument()
})

test('the new stats reads are org-scoped', async () => {
  // The overview stats block must go through THIS org's prefix -- an
  // unscoped read would put another desk's fleet size and copy volume on
  // this desk's front page.
  setRole('admin')
  const fetchMock = stubApi(statsRoutes())

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    const urls = fetchMock.mock.calls.map(([u]) => String(u))
    expect(urls).toContain('/api/orgs/1/overview')
    expect(urls.some((u) => u === '/api/overview')).toBe(false)
  })
})


test('closing a contract acknowledges instantly and keeps refreshing', async () => {
  setRole('admin')
  const routes = statsRoutes(stateWithMasterPosition)
  routes['/api/orgs/1/positions/close'] = { status: 'submitted' }
  const fetchMock = stubApi(routes)
  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  const openTile = await screen.findByRole('button', { name: /view open positions/i })
  await userEvent.click(openTile)
  const closeButtons = await screen.findAllByRole('button', { name: /^close$/i })
  await userEvent.click(closeButtons[0])
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close position/i }))

  // Instant row acknowledgment...
  expect(await screen.findByText(/closing…/i)).toBeInTheDocument()

  // ...and the follow-up burst catches the copier's resync (beyond the one
  // immediate refetch), so the row vanishes without a manual reload. The
  // first burst tick fires at ~350ms real time.
  const countState = () =>
    fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/orgs/1/state')).length
  const soonAfter = countState()
  await waitFor(() => {
    expect(countState()).toBeGreaterThan(soonAfter)
  }, { timeout: 2000 })
})

test('the copier-performance analytics live on Performance, not Overview', async () => {
  setRole('admin')
  const fetchMock = stubApi(statsRoutes())

  const { container } = render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByText('Master equity')
  // The four analytics cards and their heading moved to the Performance
  // page; Overview is the live desk, not the results review.
  expect(screen.queryByText(/copier performance/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/mirrorfleet score/i)).not.toBeInTheDocument()
  expect(container.querySelector('[data-chart="cumulative-pnl"]')).toBeNull()
  expect(container.querySelector('[data-chart="drawdown"]')).toBeNull()
  expect(container.querySelector('[data-chart="daily-pnl"]')).toBeNull()
  // ...and the analytics fetch that fed them is gone with it.
  expect(fetchMock.mock.calls.map(([u]) => String(u))
    .some((u) => u.includes('/analytics'))).toBe(false)
})


test("Today's P&L replaces the copy counter and sums every account", async () => {
  // Operators wanted one number for the whole fleet -- master and slaves --
  // instead of a fill count that said nothing about money.
  setRole('admin')
  stubApi(statsRoutes())

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByText("Today's P&L")).toBeInTheDocument()
  // The old tile must be gone, not merely pushed off screen.
  expect(screen.queryByText('Copied today')).not.toBeInTheDocument()
})

test('Total P&L shows a dash, not zero, before there is a yesterday', async () => {
  // A desk with no prior snapshot has no P&L to report. Rendering 0.00
  // would claim it broke even, which is a different and false statement.
  setRole('admin')
  stubApi({ ...statsRoutes(), '/api/orgs/1/overview': { ...overviewStats, yesterday: null } })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByText("Today's P&L")).toBeInTheDocument()
  expect(screen.getByText('needs a full day of history')).toBeInTheDocument()
})

test('a degraded account still warns after the copy-counter tile was replaced', async () => {
  // This warning used to live on "Copied today". A degraded account has
  // silently stopped copying, so losing the signal with the old tile would
  // have been a real regression.
  setRole('admin')
  stubApi({ ...statsRoutes(), '/api/orgs/1/overview': { ...overviewStats, degraded: 2 } })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByText('2 degraded')).toBeInTheDocument()
})


test('Total P&L ignores accounts that were not here yesterday', async () => {
  // The bug a trader hit: the tile subtracted yesterday's TOTAL from
  // today's TOTAL. When an org went from 18 accounts to 10 -- a broker
  // disabled them and they were reconnected -- it reported -275,112.83 of
  // "P&L" with no trade behind any of it. Only accounts present on both
  // days can be compared.
  setRole('admin')
  stubApi({
    ...statsRoutes(),
    '/api/orgs/1/overview': {
      ...overviewStats,
      // Yesterday had ONE account, carrying an enormous balance, which is
      // gone today. Its disappearance must not be reported as a loss.
      yesterday: {
        total_balance: 300000, total_equity: 300000,
        equity_by_account: { '1': 10000, '999': 290000 },
      },
    },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText("Today's P&L")
  // Account 999 vanished; its 290,000 must not surface as a loss anywhere.
  expect(screen.queryByText(/290,000|-290,000|275,112/)).not.toBeInTheDocument()
  // And the tile must state how many accounts it actually compared, so a
  // total that omits some cannot pass as a total of everything.
  expect(screen.getByText(/account.*since yesterday/i)).toBeInTheDocument()
})

test('Total P&L reports a dash when nothing is comparable', async () => {
  // Every account is new since yesterday. That is "cannot say", not
  // "broke even" -- reporting 0.00 would be a claim we cannot support.
  setRole('admin')
  stubApi({
    ...statsRoutes(),
    '/api/orgs/1/overview': {
      ...overviewStats,
      yesterday: {
        total_balance: 500, total_equity: 500,
        equity_by_account: { '777': 500 },   // no overlap with mockState
      },
    },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText("Today's P&L")
  // "cannot say" reads as needing history, never as a number.
  expect(screen.getByText('needs a full day of history')).toBeInTheDocument()
})


test('the live contracts table shows the protection on each position itself', async () => {
  // Reading protection off the master alone hides the case that matters:
  // a copy whose stop never arrived is the one holding unguarded risk.
  setRole('admin')
  const withProtection = {
    ...mockState,
    accounts: {
      ...mockState.accounts,
      '1': {
        ...(mockState.accounts as Record<string, unknown>)['1'] as object,
        open_pnl: -5,
        positions: [{
          position_id: 900, symbol: 'XAUUSD', symbol_id: 41, side: 'SELL',
          volume: 100, entry_price: 4583.46, current_price: 4586,
          stop_loss: 4600.5, take_profit: 4550.25, pnl_quote: -5,
        }],
      },
      '2': {
        ...(mockState.accounts as Record<string, unknown>)['2'] as object,
        open_pnl: -5,
        positions: [{
          // The copy that never received its protection.
          position_id: 901, symbol: 'XAUUSD', symbol_id: 41, side: 'SELL',
          volume: 100, entry_price: 4583.41, current_price: 4586,
          stop_loss: null, take_profit: null, pnl_quote: -5,
        }],
      },
    },
  }
  stubApi({ ...statsRoutes(withProtection) })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  // Open the contracts panel from the Open P&L tile.
  await userEvent.click(await screen.findByRole('button', { name: /view open positions/i }))

  // The protected position states its own levels...
  expect(await screen.findByText('4600.5')).toBeInTheDocument()
  expect(screen.getByText('4550.25')).toBeInTheDocument()
  // ...and the unprotected copy shows dashes rather than borrowing them.
  const slTpCells = document.querySelectorAll('td[data-label="SL / TP"]')
  expect(slTpCells.length).toBe(2)
  const unprotected = Array.from(slTpCells).find(
    (c) => !c.textContent?.includes('4600.5'))!
  expect(unprotected.textContent).toContain('—')
  expect(unprotected.textContent).not.toContain('4600.5')
})

test('an MT5 slave whose terminal is offline gets the warn marker, like a failed token refresh', async () => {
  setRole('admin')
  const accounts: Account[] = [
    mockAccounts[0],
    { ...mt5Account, connection_status: 'offline', mt5: { ...mt5Account.mt5!, connected: false } },
  ]
  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  const marker = await screen.findByTestId('slave-offline-marker')
  expect(marker).toHaveTextContent(/terminal offline/i)
  // It is a connection problem, not a send failure: no Degraded, and the
  // cTrader token banner stays down.
  expect(screen.queryByText('Degraded')).not.toBeInTheDocument()
  expect(screen.queryByTestId('refresh-failed-banner')).not.toBeInTheDocument()
})

// ---------- Task 6: triage-first Overview ----------

const quietRoutes = {
  '/api/orgs/1/accounts': mockAccounts,
  '/api/orgs/1/settings': mockSettings,
  '/api/orgs/1/state': mockState,
}

test('the page header names the page and the org, and sets the document title', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(document.title).toBe('Overview · MirrorFleet')
})

test('while loading, Overview shows the Loading primitive, never "Loading..." text', () => {
  setRole('admin')
  // Never resolves: the page stays in its loading state.
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')
  expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
})

test('the Attention card says all clear, and nothing else, when nothing is wrong', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  expect(card).toHaveTextContent('All clear — copying live')
  expect(within(card).queryAllByRole('listitem')).toHaveLength(0)
})

test('the all-clear line names the real state when copying is paused', async () => {
  setRole('admin')
  stubApi({ ...quietRoutes, '/api/orgs/1/settings': { copying_enabled: false, dry_run: false } })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByTestId('attention-card')).toHaveTextContent('All clear — copying paused')
})

test('a degraded follower is listed on the Attention card with an action', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], status: 'degraded' },
      mockAccounts[2],
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  const rows = within(card).getAllByRole('listitem')
  expect(rows).toHaveLength(1)
  expect(rows[0]).toHaveTextContent('Account 1002 is degraded: copies to it are failing.')
  expect(within(rows[0]).getByRole('link', { name: 'Open Accounts' }))
    .toHaveAttribute('href', '/org/1/accounts')
  expect(card).not.toHaveTextContent(/all clear/i)
})

test('a failed token refresh is an Attention row that links to Accounts', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], connection_status: 'refresh_failed' },
      mockAccounts[2],
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const row = await screen.findByTestId('refresh-failed-banner')
  expect(within(screen.getByTestId('attention-card')).getAllByRole('listitem')).toContain(row)
  expect(within(row).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
})

test('an offline terminal and a disconnected enabled follower are both listed', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], status: 'disconnected' },
      // As the copier reports it: an offline terminal is also marked degraded.
      {
        ...mt5Account, status: 'degraded', connection_status: 'offline',
        mt5: { ...mt5Account.mt5!, connected: false },
      },
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  await waitFor(() => expect(within(card).getAllByRole('listitem')).toHaveLength(2))
  expect(card).toHaveTextContent("VPS desk's terminal is offline, so copies wait until the EA reports again.")
  expect(card).toHaveTextContent('Account 1002 is not connected to its broker, so it receives no copies.')
  // One row per account: the offline terminal is not listed again as degraded.
  expect(within(card).getAllByText(/VPS desk/)).toHaveLength(1)
  expect(card).not.toHaveTextContent('VPS desk is degraded')
})

test('a failed live-state read is listed with a Retry that refetches', async () => {
  setRole('admin')
  let stateCalls = 0
  const json = (body: unknown, status = 200) => Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
  const fetchMock = vi.fn((path: string) => {
    if (path === '/api/orgs/1/accounts') return json(mockAccounts)
    if (path === '/api/orgs/1/settings') return json(mockSettings)
    if (path === '/api/orgs/1/state') {
      stateCalls += 1
      return stateCalls === 1 ? json({ detail: 'copier unreachable' }, 503) : json(mockState)
    }
    return Promise.resolve(new Response(null, { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const row = await screen.findByTestId('attention-state-error')
  expect(row).toHaveTextContent(
    'Live figures stopped refreshing (copier unreachable), so the numbers on this page may be stale.')
  await userEvent.click(within(row).getByRole('button', { name: 'Retry' }))

  await waitFor(() => expect(screen.queryByTestId('attention-state-error')).not.toBeInTheDocument())
  expect(stateCalls).toBeGreaterThanOrEqual(2)
  expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — copying live')
})

test('a margin call streamed over the socket is listed, links to Positions and can be dismissed', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  const ws = fakeSockets[fakeSockets.length - 1]
  act(() => {
    ws.onmessage?.({
      data: JSON.stringify({
        id: 7, ts: new Date().toISOString(), category: 'risk', severity: 'error',
        account_id: 2, payload: { action: 'margin_call', margin_level_threshold: 50 },
      }),
    })
  })

  const row = await screen.findByTestId('attention-margin-call')
  expect(row).toHaveTextContent(/Margin call on Account 1002/)
  expect(within(row).getByRole('link', { name: 'Open Positions' })).toHaveAttribute('href', '/org/1/positions')
  await userEvent.click(within(row).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByTestId('attention-margin-call')).not.toBeInTheDocument()
})

test('master equity appears exactly once on Overview', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText(/Master account \(1001\)/)
  expect(screen.getAllByText('12,000.00')).toHaveLength(1)
  expect(screen.getByText('Master equity')).toBeInTheDocument()
})

test('drill-downs are visible View controls, and every tile footer links onward', async () => {
  setRole('admin')
  stubApi(statsRoutes())

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  for (const name of [/view all accounts/i, /view today's fills/i, /view open positions/i, /view fleet health/i]) {
    expect(screen.getByRole('button', { name })).toHaveAttribute('aria-expanded', 'false')
  }
  for (const tile of screen.getAllByTestId('slave-tile')) {
    expect(within(tile).getByRole('link', { name: /^view/i })).toHaveAttribute('href', '/org/1/accounts')
  }
  expect(screen.getByRole('link', { name: 'View positions' })).toHaveAttribute('href', '/org/1/positions')
})

test('user-visible copy says follower, never slave', async () => {
  setRole('admin')
  stubApi(statsRoutes())

  const { container } = render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText('Copy log')
  await userEvent.click(screen.getByRole('button', { name: /view fleet health/i }))
  expect(container.textContent ?? '').not.toMatch(/slave/i)
  expect(screen.getByText('Followers')).toBeInTheDocument()
})

test('a long copy failure expands in place instead of hiding in a tooltip', async () => {
  setRole('admin')
  const longError = 'TRADING_BAD_VOLUME: volume 0.001 is below the symbol minimum of 0.01'
  stubApi({
    ...statsRoutes(),
    '/api/orgs/1/overview': {
      ...overviewStats,
      recent_copies: [{
        status: 'failed', master_position_id: 44, master_order_id: null,
        slave_account_id: 2, slave_login: 1002, slave_nickname: null,
        symbol: 'EURUSD', slave_volume: null, fill_price: null,
        error: longError, updated_at: '2026-08-18T09:00:00+00:00',
      }],
    },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const toggle = await screen.findByRole('button', { name: 'Show details' })
  // The whole reason is in the page, not cut at 24 characters.
  expect(screen.getByText(longError)).toBeInTheDocument()
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(toggle)
  expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true')
})

test('an empty org gets a four-step setup checklist instead of "no followers" text', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': [],
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': { accounts: {}, master_positions: [], pending_orders: [], drift: [] },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('setup-checklist')
  const steps = within(card).getAllByRole('listitem')
  expect(steps).toHaveLength(4)
  expect(steps[0]).toHaveTextContent('Connect the master')
  expect(steps[1]).toHaveTextContent('Add followers')
  expect(steps[2]).toHaveTextContent('Run a dry run')
  expect(steps[3]).toHaveTextContent('Go live')
  for (const step of steps) expect(step).toHaveTextContent('To do')

  expect(within(steps[0]).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
  expect(within(steps[1]).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
  expect(within(steps[2]).getByRole('link', { name: 'Open Automation' })).toHaveAttribute('href', '/org/1/automation')

  expect(screen.queryByText(/no slave accounts configured/i)).not.toBeInTheDocument()
  // An empty desk has nothing to triage yet.
  expect(screen.queryByTestId('attention-card')).not.toBeInTheDocument()
})

test('the setup checklist marks steps done from real state', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': [mockAccounts[0]], // a master, no followers yet
    '/api/orgs/1/settings': { copying_enabled: true, dry_run: true },
    '/api/orgs/1/state': mockState,
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const steps = within(await screen.findByTestId('setup-checklist')).getAllByRole('listitem')
  expect(steps[0]).toHaveTextContent('Done')   // master connected
  expect(steps[1]).toHaveTextContent('To do')  // no follower
  expect(steps[2]).toHaveTextContent('Done')   // dry-run is on
  expect(steps[3]).toHaveTextContent('To do')  // not live
  // The master card still renders beside the checklist.
  expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
})

// ---------- Task 6 review, round 1 ----------

test('a degraded master is an Attention row, never "All clear"', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [{ ...mockAccounts[0], status: 'degraded' }, mockAccounts[1], mockAccounts[2]],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  const rows = within(card).getAllByRole('listitem')
  expect(rows).toHaveLength(1)
  expect(rows[0]).toHaveTextContent('The master account is degraded — nothing is being copied.')
  expect(within(rows[0]).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
  expect(card).not.toHaveTextContent(/all clear/i)
})

test("settings the desk strip published win over the page's own load", async () => {
  setRole('admin')
  stubApi(quietRoutes) // the page's own read says copying is live
  publishSettings({ copying_enabled: false, dry_run: false })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByTestId('attention-card')).toHaveTextContent('All clear — copying paused')
})
