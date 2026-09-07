import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Automation from './Automation'
import * as apiModule from '../lib/api'

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
  master_account_id: 999, dry_run: false, copying_enabled: true, template: '{}',
  recent: [{ id: 1, received_at: '2026-09-06T17:57:15Z', outcome: 'accepted', reason: null,
             action: 'buy', symbol: 'BTCUSD', lots: 0.01, source_ip: '52.89.214.238', latency_ms: 14 }],
}

function mockWebhookRoute() {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(webhook), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
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
