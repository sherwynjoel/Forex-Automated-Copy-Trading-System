import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { REQUESTS_POLL_MS, useRequestsBadge } from './useRequestsBadge'
import * as apiModule from '../lib/api'
import type { Role } from '../lib/roles'

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

function Probe({ role, orgId = 1 }: { role: Role; orgId?: number }) {
  const badge = useRequestsBadge(orgId, role)
  return <span data-testid="badge">{badge === undefined ? 'none' : String(badge)}</span>
}

let total = 3
let summaryCalls: string[] = []

beforeEach(() => {
  total = 3
  summaryCalls = []
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as unknown as WebSocket)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/requests/summary')) {
      summaryCalls.push(url)
      return new Response(JSON.stringify({ deposits: total, withdrawals: 0, transfers: 0, payout_destinations: 0, total }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  MockWebSocket.instance = null
})

test('an admin reads the total from requests/summary and polls it every 30 s', async () => {
  render(<Probe role="admin" />)
  expect(await screen.findByText('3')).toBeInTheDocument()
  expect(summaryCalls).toEqual(['/api/orgs/1/requests/summary'])
  expect(REQUESTS_POLL_MS).toBe(30000)

  total = 5
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  await waitFor(() => expect(summaryCalls).toHaveLength(2))
  expect(await screen.findByText('5')).toBeInTheDocument()
})

test('a control event refetches the total within the live-refresh burst', async () => {
  render(<Probe role="admin" />)
  await screen.findByText('3')
  total = 4
  act(() => { MockWebSocket.instance!.emit({ category: 'control', payload: { action: 'investor_deposit_noticed' } }) })
  await act(async () => { await vi.advanceTimersByTimeAsync(300) })
  await waitFor(() => expect(summaryCalls).toHaveLength(2))
  expect(await screen.findByText('4')).toBeInTheDocument()
})

test('viewers and investors get undefined, no request and no socket', async () => {
  const { unmount } = render(<Probe role="viewer" />)
  expect(screen.getByTestId('badge')).toHaveTextContent('none')
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  expect(summaryCalls).toEqual([])
  expect(apiModule.eventsSocket).not.toHaveBeenCalled()
  unmount()
  render(<Probe role="investor" />)
  expect(screen.getByTestId('badge')).toHaveTextContent('none')
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  expect(summaryCalls).toEqual([])
  expect(apiModule.eventsSocket).not.toHaveBeenCalled()
})
