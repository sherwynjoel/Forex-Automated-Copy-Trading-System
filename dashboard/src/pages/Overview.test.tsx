import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Overview from './Overview'
import type { Account, ApiState, Settings, StateSnapshot } from '../lib/types'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

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
})

test('renders master card with equity/balance/pnl', async () => {
  setRole('owner')
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
    expect(screen.getByText(/Master Account \(1001\)/)).toBeInTheDocument()
  })

  // Check master card displays the values
  expect(screen.getByText('$12000.00')).toBeInTheDocument()
  expect(screen.getByText('$10000.00')).toBeInTheDocument()
  expect(screen.getByText('$2000.00')).toBeInTheDocument()
})

test('master card renders equity/balance/P&L read from the nested accounts block', async () => {
  setRole('owner')
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
    expect(screen.getByText(/Master Account \(1001\)/)).toBeInTheDocument()
  })

  // Account 1 is the master; its numbers come from envelope.accounts['1'].
  expect(screen.getByText('$12000.00')).toBeInTheDocument() // equity
  expect(screen.getByText('$10000.00')).toBeInTheDocument() // balance
  expect(screen.getByText('$2000.00')).toBeInTheDocument() // open P&L

  // ...and a slave tile's stats come from the same nested block, so a
  // regression cannot hide behind the master card alone.
  expect(screen.getByText('$5500.00')).toBeInTheDocument() // slave 2 equity
  expect(screen.getByText('$5000.00')).toBeInTheDocument() // slave 2 balance
})

test('renders no account stats when the accounts block is empty, without crashing', async () => {
  setRole('owner')
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

  await waitFor(() => {
    expect(screen.getByText('Copying Status')).toBeInTheDocument()
  })

  expect(screen.queryByText(/Master Account \(1001\)/)).not.toBeInTheDocument()
  expect(screen.queryByText('$12000.00')).not.toBeInTheDocument()
  // Slave tiles still render (they come from /api/orgs/1/accounts), just without stats.
  expect(screen.getAllByTestId('slave-tile').length).toBeGreaterThanOrEqual(2)
})

test('renders slave tiles with status icons', async () => {
  setRole('owner')
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
  await waitFor(() => {
    expect(screen.getByText(/1002/)).toBeInTheDocument()
  })

  // Check slave tiles are rendered with login numbers
  expect(screen.getByText(/1002/)).toBeInTheDocument() // Slave 1 (ok)
  expect(screen.getByText(/1003/)).toBeInTheDocument() // Slave 2 (degraded)

  // Check status icons exist (use emoji checks or data-testid)
  const tiles = screen.getAllByTestId(/slave-tile/)
  expect(tiles.length).toBeGreaterThanOrEqual(2)
})

test('kill switch confirms then PUTs copying_enabled false', async () => {
  setRole('owner')
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  // Mock window.confirm
  const confirmMock = vi.fn().mockReturnValue(true)
  vi.stubGlobal('confirm', confirmMock)

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  // Wait for data to load
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /stop copying/i })).toBeInTheDocument()
  })

  const killSwitchButton = screen.getByRole('button', { name: /stop copying/i })
  await userEvent.click(killSwitchButton)

  // Verify confirm was called
  expect(confirmMock).toHaveBeenCalled()

  // Verify PUT request was made with copying_enabled: false
  await waitFor(() => {
    const putCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
    )
    expect(putCall).toBeDefined()
    const body = JSON.parse(putCall![1]?.body as string)
    expect(body.copying_enabled).toBe(false)
  })
})

test('kill switch does not PUT if confirm is rejected', async () => {
  setRole('owner')
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  const confirmMock = vi.fn().mockReturnValue(false)
  vi.stubGlobal('confirm', confirmMock)

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /stop copying/i })).toBeInTheDocument()
  })

  const killSwitchButton = screen.getByRole('button', { name: /stop copying/i })
  await userEvent.click(killSwitchButton)

  expect(confirmMock).toHaveBeenCalled()

  // No PUT should be made
  const putCall = fetchMock.mock.calls.find(
    (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
  )
  expect(putCall).toBeUndefined()
})

test('per-slave pause posts to /api/orgs/1/control/pause with account_id', async () => {
  setRole('owner')
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
  setRole('owner')
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
})

test('does not show refresh-failed banner when all connections are active', async () => {
  setRole('owner')
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
    expect(screen.getByText(/Master Account \(1001\)/)).toBeInTheDocument()
  })

  expect(screen.queryByTestId('refresh-failed-banner')).not.toBeInTheDocument()
})

test('shows prominent refresh-failed banner naming the affected account', async () => {
  setRole('owner')
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
  setRole('owner')
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
  const masterHeading = screen.getByText(/Master Account \(1001\)/)
  // eslint-disable-next-line no-bitwise
  expect(banner.compareDocumentPosition(masterHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('slave tile shows Degraded status from account.status, not connection_status', async () => {
  setRole('owner')
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

  await waitFor(() => {
    expect(screen.getByText(/1002/)).toBeInTheDocument()
  })

  // account.status === 'degraded' must drive the degraded badge, even though
  // connection_status is 'active' (connection_status never holds 'degraded').
  expect(screen.getByText('Degraded')).toBeInTheDocument()
})

test('degraded slave tile shows the last_error reason', async () => {
  setRole('owner')
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
})

test('non-degraded slave tile never shows a last_error message', async () => {
  setRole('owner')
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

  await waitFor(() => {
    expect(screen.getByText(/1002/)).toBeInTheDocument()
  })

  expect(screen.queryByTestId('slave-last-error')).not.toBeInTheDocument()
})

test('slave tile shows a refresh-failed marker distinct from degraded styling', async () => {
  setRole('owner')
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
    setRole('owner')
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
      expect(screen.getByText(/Master Account \(1001\)/)).toBeInTheDocument()
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

// ---------- N1: the dry-run toggle ----------

test('dry-run toggle PUTs dry_run: true when enabling', async () => {
  setRole('owner')
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings, // dry_run: false
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument()
  })

  const toggle = screen.getByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run on/i)
  await userEvent.click(toggle)

  await waitFor(() => {
    const putCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
    )
    expect(putCall).toBeDefined()
    // The real value must be in the body -- this is the dashboard half of
    // the seam where the api forwarded a hardcoded empty body and the copier
    // read the absent "enabled" as false.
    expect(JSON.parse(putCall![1]?.body as string)).toEqual({ dry_run: true })
  })
})

test('enabling dry-run needs no confirmation (it is the safe direction)', async () => {
  setRole('owner')
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })
  const confirmMock = vi.fn().mockReturnValue(false)
  vi.stubGlobal('confirm', confirmMock)

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument())
  await userEvent.click(screen.getByTestId('dry-run-toggle'))

  expect(confirmMock).not.toHaveBeenCalled()
  await waitFor(() => {
    const putCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
    )
    expect(putCall).toBeDefined()
  })
})

test('disabling dry-run confirms first, then PUTs dry_run: false', async () => {
  setRole('owner')
  const dryRunSettings: Settings = { copying_enabled: true, dry_run: true }
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': dryRunSettings,
    '/api/orgs/1/state': mockState,
  })
  const confirmMock = vi.fn().mockReturnValue(true)
  vi.stubGlobal('confirm', confirmMock)

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument())
  const toggle = screen.getByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run off/i)
  await userEvent.click(toggle)

  expect(confirmMock).toHaveBeenCalled()
  await waitFor(() => {
    const putCall = fetchMock.mock.calls.find(
      (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
    )
    expect(putCall).toBeDefined()
    expect(JSON.parse(putCall![1]?.body as string)).toEqual({ dry_run: false })
  })
})

test('disabling dry-run does not PUT if confirm is rejected', async () => {
  setRole('owner')
  const dryRunSettings: Settings = { copying_enabled: true, dry_run: true }
  const fetchMock = stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': dryRunSettings,
    '/api/orgs/1/state': mockState,
  })
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument())
  await userEvent.click(screen.getByTestId('dry-run-toggle'))

  const putCall = fetchMock.mock.calls.find(
    (call) => call[1]?.method === 'PUT' && call[0].includes('/api/orgs/1/settings')
  )
  expect(putCall).toBeUndefined()
})

test('the dry-run badge reflects the toggled state without a reload', async () => {
  setRole('owner')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings, // dry_run: false -> no badge initially
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await waitFor(() => expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument())
  expect(screen.queryByText(/^DRY RUN$/)).not.toBeInTheDocument()

  await userEvent.click(screen.getByTestId('dry-run-toggle'))

  await waitFor(() => {
    expect(screen.getByText(/^DRY RUN$/)).toBeInTheDocument()
  })
})

// ---------- Task 17: KillSwitch role gating ----------

test('kill switch is hidden for a viewer (below control)', async () => {
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

  await waitFor(() => {
    expect(screen.getByText('Copying Status')).toBeInTheDocument()
  })

  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

test('kill switch is hidden for a trader (below control)', async () => {
  setRole('trader')
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

  await waitFor(() => {
    expect(screen.getByText('Copying Status')).toBeInTheDocument()
  })

  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

test('kill switch is visible for an admin (control)', async () => {
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

  expect(await screen.findByRole('button', { name: /stop copying/i })).toBeInTheDocument()
  expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument()
})
