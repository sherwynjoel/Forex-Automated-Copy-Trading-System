import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { UNREAD_POLL_MS, useUnreadCount } from './useUnreadCount'

function stubAnswers(...answers: unknown[]) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(
    JSON.stringify(answers.length > 1 ? answers.shift() : answers[0]),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('asks the org for its unread count and polls it every 10 s', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = stubAnswers({ count: 3 }, { count: 1 })
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(result.current.count).toBe(3))
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/7/notifications/unread-count')
  await act(async () => { vi.advanceTimersByTime(UNREAD_POLL_MS) })
  await waitFor(() => expect(result.current.count).toBe(1))
})

test('an answer without a number leaves the count unknown', async () => {
  const fetchMock = stubAnswers({})
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  expect(result.current.count).toBeUndefined()
})

test('an older poll answering after a newer refresh never lands', async () => {
  const json = (payload: unknown) => new Response(JSON.stringify(payload),
    { status: 200, headers: { 'Content-Type': 'application/json' } })
  let releaseFirst!: (r: Response) => void
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((res) => { releaseFirst = res }))
    .mockImplementation(async () => json({ count: 0 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useUnreadCount(7))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  act(() => { result.current.refresh() })      // e.g. after a mark-read
  await waitFor(() => expect(result.current.count).toBe(0))
  await act(async () => {
    releaseFirst(json({ count: 5 }))
    await new Promise((r) => setTimeout(r, 0))
  })
  expect(result.current.count).toBe(0)
})
