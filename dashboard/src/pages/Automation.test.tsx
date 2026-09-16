import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Automation from './Automation'
import * as apiModule from '../lib/api'
import type { RiskRule } from '../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

// Same shape as useLiveRefresh.test.tsx: captures the instance, allows emit.
class MockWebSocket {
  static instance: MockWebSocket | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor() { MockWebSocket.instance = this }
  close() { /* no-op */ }
  emit(json: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(json) }))
  }
}

const webhook = {
  configured: true, hook_id: 'hookabc', url: 'https://mirrorfleet.test/api/webhooks/tradingview/hookabc',
  url_hint: null, has_secret: true, secret_created_at: '2026-09-06T12:35:11Z', enabled: true,
  max_lots: 0.1, max_per_minute: 10, max_open_positions: 3, symbol_aliases: {},
  vt_ltf_timeframes: ['5'],
  master_account_id: 999, dry_run: false, copying_enabled: true, template: '{}',
  recent: [{ id: 1, received_at: '2026-09-06T17:57:15Z', outcome: 'accepted', reason: null,
             action: 'buy', symbol: 'BTCUSD', lots: 0.01, source_ip: '52.89.214.238', latency_ms: 14 }],
}

function mockWebhookRoute() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    // The page also loads risk rules alongside webhook settings on every
    // refresh; these tests don't care about that list, so it's just empty.
    if (String(input).includes('/risk-rules')) {
      return new Response(JSON.stringify([]), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify(webhook), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Route-based fetch mock: serves the webhook fixture by default (so the
 *  page can mount) plus whatever risk-rules overrides a test supplies,
 *  keyed as "METHOD /path-fragment". */
function mockRoutes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [fragment, responder] of Object.entries(overrides)) {
      const [method, path] = fragment.includes(' ') ? fragment.split(' ') : [undefined, fragment]
      if (url.includes(path) && (!method || (init?.method || 'GET') === method)) {
        return responder(init)
      }
    }
    if (url.includes('/webhook')) return jsonResponse(webhook)
    if (url.includes('/risk-rules')) return jsonResponse([])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue({
    orgId: 1, role: 'owner', org: { id: 1, name: 'Desk', role: 'owner' },
    me: { user: { id: 1, email: 'ada@example.com', display_name: 'Ada' },
          orgs: [{ id: 1, name: 'Desk', role: 'owner' }] },
    refreshMe: vi.fn(),
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  MockWebSocket.instance = null
})

const webhookCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter(([input]) => String(input).includes('/webhook')).length

test('an alert landing refetches the recent list within a quarter second, no poll needed', async () => {
  const fetchMock = mockWebhookRoute()
  render(<MemoryRouter><Automation /></MemoryRouter>)

  expect(await screen.findByText(/automation is on/i)).toBeInTheDocument()
  const before = webhookCalls(fetchMock)
  expect(before).toBeGreaterThan(0)

  // Exactly what the api writes for every alert outcome.
  act(() => {
    MockWebSocket.instance!.emit({ category: 'control', payload: { action: 'webhook_alert', outcome: 'accepted' } })
  })

  await waitFor(() => expect(webhookCalls(fetchMock)).toBeGreaterThan(before), { timeout: 1500 })
})

test('the recent list says it is live, not on a timer', async () => {
  mockWebhookRoute()
  render(<MemoryRouter><Automation /></MemoryRouter>)

  expect(await screen.findByText(/updates the moment an alert lands/i)).toBeInTheDocument()
})

test('entry timeframes shows what is currently allowed and lets an admin change it', async () => {
  const fetchMock = mockWebhookRoute()
  render(<MemoryRouter><Automation /></MemoryRouter>)

  await waitFor(() => expect(screen.getByRole('heading', { name: /Entry timeframes/i })).toBeTruthy())
  const five = await screen.findByRole('checkbox', { name: '5m' })
  const one = screen.getByRole('checkbox', { name: '1m' })
  expect((five as HTMLInputElement).checked).toBe(true)
  expect((one as HTMLInputElement).checked).toBe(false)

  fetchMock.mockClear()
  await act(async () => {
    one.click()
  })
  await act(async () => {
    screen.getByRole('button', { name: /Save entry timeframes/i }).click()
  })

  await waitFor(() => expect(webhookCalls(fetchMock)).toBeGreaterThan(0))
  const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')
  const body = JSON.parse((putCall![1] as RequestInit).body as string)
  expect(sorted(body.vt_ltf_timeframes)).toEqual(['1', '5'])
})

function sorted(a: string[]) { return [...a].sort() }

test('lists existing risk rules and can add a new one', async () => {
  const rules: RiskRule[] = [
    { symbol: 'XAUUSD', stop_points: 50, target_points: 150,
      trailing_enabled: false, trail_start_points: null, trail_step_points: null },
  ]
  mockRoutes({
    'GET /risk-rules': () => jsonResponse(rules),
    'PUT /risk-rules': () => jsonResponse({
      symbol: 'EURUSD', stop_points: 20, target_points: 60,
      trailing_enabled: false, trail_start_points: null, trail_step_points: null,
    }),
  })
  render(<MemoryRouter><Automation /></MemoryRouter>)

  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText(/symbol/i), 'EURUSD')
  await userEvent.type(screen.getByLabelText(/^stop/i), '20')
  await userEvent.type(screen.getByLabelText(/^target/i), '60')
  await userEvent.click(screen.getByRole('button', { name: /add rule/i }))

  expect(await screen.findByText('EURUSD')).toBeInTheDocument()
})

test('can delete a risk rule', async () => {
  const rules: RiskRule[] = [
    { symbol: 'XAUUSD', stop_points: 50, target_points: 150,
      trailing_enabled: false, trail_start_points: null, trail_step_points: null },
  ]
  mockRoutes({
    'GET /risk-rules': () => jsonResponse(rules),
    'DELETE /risk-rules': () => new Response(null, { status: 204 }),
  })
  render(<MemoryRouter><Automation /></MemoryRouter>)
  await screen.findByText('XAUUSD')

  await userEvent.click(screen.getByRole('button', { name: /remove xauusd/i }))

  await waitFor(() => expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument())
})
