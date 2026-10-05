import { afterEach, expect, test, vi } from 'vitest'
import { markAllRead, markRead, withAllRead } from './notificationActions'
import { notificationFixture } from '../test/portalFixtures'

const READ_AT = '2026-10-05T10:00:00Z'

function stub(payload: unknown) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(
    JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.unstubAllGlobals() })

test('markRead posts for an unread row and passes a read one straight through', async () => {
  const unread = notificationFixture({ id: 31 })
  const read = notificationFixture({ id: 30, read_at: READ_AT })
  const fetchMock = stub({ ...unread, read_at: READ_AT })
  expect(await markRead(1, read)).toBe(read)
  expect(fetchMock).not.toHaveBeenCalled()
  expect((await markRead(1, unread)).read_at).toBe(READ_AT)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications/31/read')
  expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
})

test('markAllRead posts read-all; withAllRead keeps each first read_at', async () => {
  const fetchMock = stub({ updated: 1 })
  await markAllRead(1)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications/read-all')
  expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
  const list = withAllRead([notificationFixture({ id: 31 }),
                            notificationFixture({ id: 30, read_at: '2026-10-01T10:00:00Z' })], READ_AT)
  expect(list.map((n) => n.read_at)).toEqual([READ_AT, '2026-10-01T10:00:00Z'])
})
