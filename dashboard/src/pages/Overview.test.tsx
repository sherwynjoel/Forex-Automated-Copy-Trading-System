import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Overview from './Overview'
import type { Account, Settings, StateSnapshot } from '../lib/types'

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
  shards: 4,
}

const mockState: StateSnapshot = {
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

afterEach(() => vi.unstubAllGlobals())

test('renders master card with equity/balance/pnl', async () => {
  stubApi({
    '/api/accounts': mockAccounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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

test('renders slave tiles with status icons', async () => {
  stubApi({
    '/api/accounts': mockAccounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  const fetchMock = stubApi({
    '/api/accounts': mockAccounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
      (call) => call[1]?.method === 'PUT' && call[0].includes('/api/settings')
    )
    expect(putCall).toBeDefined()
    const body = JSON.parse(putCall![1]?.body as string)
    expect(body.copying_enabled).toBe(false)
  })
})

test('kill switch does not PUT if confirm is rejected', async () => {
  const fetchMock = stubApi({
    '/api/accounts': mockAccounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
    (call) => call[1]?.method === 'PUT' && call[0].includes('/api/settings')
  )
  expect(putCall).toBeUndefined()
})

test('per-slave pause posts to /api/control/pause with account_id', async () => {
  const routes: Record<string, unknown> = {
    '/api/accounts': mockAccounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
  }

  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    // Handle pause/resume endpoints
    if (path.includes('/api/control/pause') || path.includes('/api/control/resume')) {
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
      (call) => call[1]?.method === 'POST' && call[0].includes('/api/control/pause')
    )
    expect(pauseCall).toBeDefined()
    const body = JSON.parse(pauseCall![1]?.body as string)
    expect(body.account_id).toBe(2)
  })
})

test('shows dry-run badge when dry_run enabled', async () => {
  const dryRunSettings: Settings = {
    copying_enabled: true,
    dry_run: true,
    shards: 4,
  }

  stubApi({
    '/api/accounts': mockAccounts,
    '/api/settings': dryRunSettings,
    '/api/state': mockState,
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
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'refresh_failed' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'refresh_failed' },
    { ...mockAccounts[1], connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], status: 'degraded', connection_status: 'active' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  const accounts: Account[] = [
    { ...mockAccounts[0], connection_status: 'active' },
    { ...mockAccounts[1], connection_status: 'refresh_failed' },
    { ...mockAccounts[2], connection_status: 'active' },
  ]

  stubApi({
    '/api/accounts': accounts,
    '/api/settings': mockSettings,
    '/api/state': mockState,
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
  'polls /api/state every 5 seconds',
  async () => {
    // Spy on setInterval to verify it's called with 5000ms
    const setIntervalSpy = vi.spyOn(global, 'setInterval')

    const fetchMock = stubApi({
      '/api/accounts': mockAccounts,
      '/api/settings': mockSettings,
      '/api/state': mockState,
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

    // Verify that /api/state was called during initial load
    const initialStateCalls = fetchMock.mock.calls.filter((call) => call[0] === '/api/state').length
    expect(initialStateCalls).toBeGreaterThan(0)

    // Verify that setInterval was called with 5000ms interval for polling
    const pollingInterval = setIntervalSpy.mock.calls.find(
      (call) => typeof call[1] === 'number' && call[1] === 5000
    )
    expect(pollingInterval).toBeDefined()

    setIntervalSpy.mockRestore()
  }
)
