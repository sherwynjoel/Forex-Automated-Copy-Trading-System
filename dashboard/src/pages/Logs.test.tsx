import { render, screen, waitFor } from '@testing-library/react'
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
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(events), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)

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
  const severitySelect = screen.getByDisplayValue('all')
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
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(events), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Logs />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('2024-01-01T10:00:00Z')).toBeInTheDocument()
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
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(initialEvents), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)

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
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(initialEvents), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)

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
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  )
  const severitySelect = screen.getByDisplayValue('all')
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
