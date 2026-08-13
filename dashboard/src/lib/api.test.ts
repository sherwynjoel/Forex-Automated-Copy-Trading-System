import { afterEach, expect, test, vi } from 'vitest'
import { api } from './api'

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

test('api throws on non-2xx', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('boom', { status: 500 })))
  await expect(api('/api/accounts')).rejects.toThrow('500')
})
