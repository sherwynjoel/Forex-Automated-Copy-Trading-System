import { render, act } from '@testing-library/react'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { useLiveRefresh } from './useLiveRefresh'
import * as apiModule from '../lib/api'

// Same shape as Logs.test.tsx's mock: captures the instance, allows emit.
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

function Probe({ onRefetch }: { onRefetch: () => void }) {
  useLiveRefresh(onRefetch)
  return null
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as any)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  MockWebSocket.instance = null
})

test('a relevant event triggers an immediate refetch and a follow-up', () => {
  const refetch = vi.fn()
  render(<Probe onRefetch={refetch} />)

  act(() => {
    MockWebSocket.instance!.emit({ category: 'master_event' })
  })
  expect(refetch).not.toHaveBeenCalled()

  act(() => {
    vi.advanceTimersByTime(250)
  })
  expect(refetch).toHaveBeenCalledTimes(1)

  // Second refetch after the copier's own debounced resync had time to land.
  act(() => {
    vi.advanceTimersByTime(1750)
  })
  expect(refetch).toHaveBeenCalledTimes(2)
})

test('a burst of events collapses into one refetch pair', () => {
  const refetch = vi.fn()
  render(<Probe onRefetch={refetch} />)

  act(() => {
    MockWebSocket.instance!.emit({ category: 'master_event' })
    MockWebSocket.instance!.emit({ category: 'slave_action' })
    MockWebSocket.instance!.emit({ category: 'slave_action' })
  })
  act(() => {
    vi.advanceTimersByTime(2000)
  })
  expect(refetch).toHaveBeenCalledTimes(2)
})

test('irrelevant and malformed events are ignored', () => {
  const refetch = vi.fn()
  render(<Probe onRefetch={refetch} />)

  act(() => {
    MockWebSocket.instance!.emit({ category: 'auth' })
    MockWebSocket.instance!.onmessage!(new MessageEvent('message', { data: 'not-json{' }))
  })
  act(() => {
    vi.advanceTimersByTime(3000)
  })
  expect(refetch).not.toHaveBeenCalled()
})
