import { afterEach, expect, test, vi, beforeEach } from 'vitest'
import { api } from './api'

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => vi.unstubAllGlobals())

test('api attaches CSRF header from cookie', async () => {
  document.cookie = 'csrf=tok123'
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), { status: 200 })
  )
  vi.stubGlobal('fetch', fetchMock)
  await api('/api/settings', { method: 'PUT', body: JSON.stringify({ dry_run: true }) })
  const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
  expect(headers['X-CSRF-Token']).toBe('tok123')
})

test('api adds default Content-Type when body is present', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), { status: 200 })
  )
  vi.stubGlobal('fetch', fetchMock)
  await api('/api/settings', { method: 'PUT', body: JSON.stringify({ dry_run: true }) })
  const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
  expect(headers['Content-Type']).toBe('application/json')
})

test('api throws on non-2xx', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('boom', { status: 500 })))
  await expect(api('/api/accounts')).rejects.toThrow('500')
})

test('api redirects to /login on 401 from non-login endpoint', async () => {
  const originalLocation = window.location.href
  const locationReplace = vi.fn()
  Object.defineProperty(window, 'location', {
    value: { href: originalLocation, reload: locationReplace },
    writable: true,
  })

  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Unauthorized', { status: 401 })))
  await expect(api('/api/accounts')).rejects.toThrow('Unauthorized')
  expect(window.location.href).toBe('/login')
})

test('api does NOT redirect on 401 from /api/login', async () => {
  const originalLocation = window.location.href
  Object.defineProperty(window, 'location', {
    value: { href: originalLocation },
    writable: true,
  })

  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Unauthorized', { status: 401 })))
  await expect(api('/api/login', { method: 'POST', body: JSON.stringify({ password: 'wrong' }) })).rejects.toThrow(
    '401'
  )
  // Location should NOT have changed (no redirect)
  expect(window.location.href).toBe(originalLocation)
})
