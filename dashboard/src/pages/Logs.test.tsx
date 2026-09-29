import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Logs from './Logs'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

// Mock WebSocket class that captures instance and allows emit
class MockWebSocket {
  static instance: MockWebSocket | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor() {
    MockWebSocket.instance = this
  }

  close() {
    // no-op
  }

  emit(json: object) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data: JSON.stringify(json) }))
    }
  }
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  vi.spyOn(apiModule, 'eventsSocket').mockReturnValue(new MockWebSocket() as any)
})

afterEach(() => {
  vi.restoreAllMocks()
  MockWebSocket.instance = null
})

const ACCOUNTS = [
  { ctid_trader_account_id: 123, trader_login: 9001, nickname: 'Gold master' },
  { ctid_trader_account_id: 124, trader_login: 9002, nickname: null },
]

// Route by URL so each request gets its own fresh Response: the page loads
// the org's accounts (for the Account filter) alongside the events, and a
// Response body can only be read once. `events` is read per call, so a test
// can change what the next events request returns.
function stubFetch(routes: { events: unknown[] }) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    const body = url.includes('/accounts') ? ACCOUNTS : url.includes('/events') ? routes.events : []
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('fetches events with filter query params from the org-scoped route', async () => {
  const events = [
    {
      id: 1,
      ts: '2024-01-01T10:00:00Z',
      account_id: 123,
      category: 'position_open',
      severity: 'info',
      latency_ms: 50,
      payload: { symbol: 'EURUSD' },
    },
  ]
  const routes = { events: events as unknown[] }
  const fetchMock = stubFetch(routes)

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  // Wait for initial fetch
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/orgs/1/events'), expect.any(Object))
  })

  // Clear previous calls
  fetchMock.mockClear()

  // Change severity filter
  const severitySelect = screen.getByLabelText('Severity')
  await userEvent.selectOptions(severitySelect, 'error')

  // Wait for new fetch with severity parameter
  await waitFor(() => {
    const calls = fetchMock.mock.calls
    expect(calls.length).toBeGreaterThan(0)
    const eventCall = calls.find(
      (call) => typeof call[0] === 'string' && call[0].includes('/api/orgs/1/events')
    )
    expect(eventCall).toBeDefined()
    const url = new URL(eventCall![0] as string, 'http://localhost')
    expect(url.searchParams.get('severity')).toBe('error')
  })
})

test('renders severity-coded rows', async () => {
  const events = [
    {
      id: 1,
      ts: '2024-01-01T10:00:00Z',
      account_id: 123,
      category: 'position_open',
      severity: 'info',
      latency_ms: 50,
      payload: { symbol: 'EURUSD' },
    },
    {
      id: 2,
      ts: '2024-01-01T11:00:00Z',
      account_id: 124,
      category: 'position_close',
      severity: 'error',
      latency_ms: 100,
      payload: { error: 'Connection lost' },
    },
  ]
  const routes = { events: events as unknown[] }
  stubFetch(routes)

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  await waitFor(() => {
    // Timestamps render human-readable; the raw ISO stays on the title attribute.
    expect(screen.getByTitle('2024-01-01T10:00:00Z')).toBeInTheDocument()
    expect(screen.getByText('position_open')).toBeInTheDocument()
    expect(screen.getByText('position_close')).toBeInTheDocument()
  })

  // Check that severity is color-coded (error should have different style)
  const errorSeverityCells = screen.getAllByText('error')
  const errorSeveritySpan = errorSeverityCells.find((el) => el.classList.contains('bg-loss-wash'))
  expect(errorSeveritySpan).toBeDefined()
  expect(errorSeveritySpan).toHaveClass('bg-loss-wash')
})

test('live websocket event prepends a row, connecting with the org id', async () => {
  const initialEvents = [
    {
      id: 1,
      ts: '2024-01-01T10:00:00Z',
      account_id: 123,
      category: 'position_open',
      severity: 'info',
      latency_ms: 50,
      payload: { symbol: 'EURUSD' },
    },
  ]
  const routes = { events: initialEvents as unknown[] }
  stubFetch(routes)

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('position_open')).toBeInTheDocument()
  })

  // Wait for WebSocket to be created
  await waitFor(() => {
    expect(MockWebSocket.instance).not.toBeNull()
  })
  expect(apiModule.eventsSocket).toHaveBeenCalledWith(1)

  // Emit a new event via WebSocket
  const newEvent = {
    id: 2,
    ts: '2024-01-01T11:00:00Z',
    account_id: 124,
    category: 'trade_executed',
    severity: 'info',
    latency_ms: 75,
    payload: { trade_id: 'T123' },
  }
  MockWebSocket.instance?.emit(newEvent)

  await waitFor(() => {
    expect(screen.getByText('trade_executed')).toBeInTheDocument()
  })
})

test('live rows respect current severity filter', async () => {
  const initialEvents = [
    {
      id: 1,
      ts: '2024-01-01T10:00:00Z',
      account_id: 123,
      category: 'position_open',
      severity: 'info',
      latency_ms: 50,
      payload: { symbol: 'EURUSD' },
    },
  ]
  const routes = { events: initialEvents as unknown[] }
  stubFetch(routes)

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('position_open')).toBeInTheDocument()
  })

  // Wait for WebSocket to be created
  await waitFor(() => {
    expect(MockWebSocket.instance).not.toBeNull()
  })

  // Set severity filter to 'error' - this will refetch events and they'll come back empty
  routes.events = []
  const severitySelect = screen.getByLabelText('Severity')
  await userEvent.selectOptions(severitySelect, 'error')

  // Wait for the refetch to complete
  await waitFor(() => {
    expect(screen.getByText('No events found')).toBeInTheDocument()
  })

  // Emit an info event (should be filtered out)
  const infoEvent = {
    id: 2,
    ts: '2024-01-01T11:00:00Z',
    account_id: 124,
    category: 'heartbeat',
    severity: 'info',
    latency_ms: 5,
    payload: {},
  }
  MockWebSocket.instance?.emit(infoEvent)

  // Wait a bit to ensure the info event is not added
  await new Promise((resolve) => setTimeout(resolve, 100))
  expect(screen.queryByText('heartbeat')).not.toBeInTheDocument()

  // Emit an error event (should be added)
  const errorEvent = {
    id: 3,
    ts: '2024-01-01T12:00:00Z',
    account_id: 124,
    category: 'position_close',
    severity: 'error',
    latency_ms: 150,
    payload: { error: 'Failed' },
  }
  MockWebSocket.instance?.emit(errorEvent)

  await waitFor(() => {
    expect(screen.getByText('position_close')).toBeInTheDocument()
  })
})

test('relay frames without id/ts (the quotes stream) never enter the log', async () => {
  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )
  await waitFor(() => {
    expect(apiModule.eventsSocket).toHaveBeenCalled()
  })

  // A quotes price frame rides the same socket but is NOT an audit event.
  MockWebSocket.instance?.emit({
    category: 'quotes',
    org_id: 1,
    payload: { quotes: { BTCUSD: { bid: 1, ask: 2 } }, accounts: {} },
  })

  await new Promise((resolve) => setTimeout(resolve, 100))
  expect(screen.queryByText('quotes')).not.toBeInTheDocument()
})

test('the log names who performed an operator action, and says system otherwise', async () => {
  const events = [
    {
      id: 1, ts: '2026-08-22T10:00:00Z', account_id: 123,
      category: 'control', severity: 'warning', latency_ms: null,
      payload: { action: 'kill_switch' }, actor_email: 'ada@example.com',
    },
    {
      id: 2, ts: '2026-08-22T10:00:01Z', account_id: 123,
      category: 'slave_action', severity: 'info', latency_ms: 12,
      payload: { action: 'copy_fill' }, actor_email: null,
    },
  ]
  vi.spyOn(apiModule, 'orgApi').mockImplementation(
    async (_org: number, path: string) => (path.startsWith('accounts') ? ACCOUNTS : events) as never,
  )

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  expect(await screen.findByText('ada@example.com')).toBeInTheDocument()
  // The copier acting on its own is not attributed to a person.
  expect(screen.getByText('system')).toBeInTheDocument()
})

test('every filter has an accessible name, and the account filter lists the org accounts by name', async () => {
  stubFetch({ events: [] })

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  const account = screen.getByLabelText('Account')
  expect(account).toHaveRole('combobox')
  expect(screen.getByLabelText('Severity')).toHaveRole('combobox')
  expect(screen.getByLabelText('Category')).toBeInTheDocument()
  expect(screen.getByLabelText('Since')).toBeInTheDocument()

  // Options are the accounts by nickname, falling back to the login; the
  // value stays the account id, so the events request is unchanged.
  expect(await screen.findByRole('option', { name: 'Gold master' })).toHaveValue('123')
  expect(screen.getByRole('option', { name: '9002' })).toHaveValue('124')
  expect(screen.getByRole('option', { name: 'All accounts' })).toHaveValue('')
  // Severity options read as words, their values unchanged.
  expect(screen.getByRole('option', { name: 'Warning' })).toHaveValue('warning')
})

test('choosing an account filters the events request by its id', async () => {
  const fetchMock = stubFetch({ events: [] })

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  await screen.findByRole('option', { name: 'Gold master' })
  fetchMock.mockClear()
  await userEvent.selectOptions(screen.getByLabelText('Account'), '123')

  await waitFor(() => {
    const eventCall = fetchMock.mock.calls.find((call) => String(call[0]).includes('/api/orgs/1/events'))
    expect(eventCall).toBeDefined()
    const url = new URL(String(eventCall![0]), 'http://localhost')
    expect(url.searchParams.get('account_id')).toBe('123')
  })
})

test('the live list keeps the newest 500 events', async () => {
  stubFetch({ events: [] })

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  expect(await screen.findByText('No events found')).toBeInTheDocument()
  await waitFor(() => {
    expect(MockWebSocket.instance).not.toBeNull()
  })

  act(() => {
    for (let i = 1; i <= 501; i++) {
      MockWebSocket.instance?.emit({
        id: i,
        ts: '2024-01-01T10:00:00Z',
        account_id: 123,
        category: `cat_${i}`,
        severity: 'info',
        latency_ms: 1,
        payload: {},
      })
    }
  })

  // Newest first: the 501st is on top and the very first one fell off.
  expect(await screen.findByText('cat_501')).toBeInTheDocument()
  expect(screen.queryByText('cat_1')).not.toBeInTheDocument()
  expect(screen.getByText('cat_2')).toBeInTheDocument()
  const bodyRows = screen.getAllByRole('row').filter((r) => r.closest('tbody'))
  expect(bodyRows).toHaveLength(500)
})
