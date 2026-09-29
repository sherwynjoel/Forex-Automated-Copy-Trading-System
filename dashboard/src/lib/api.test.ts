import { afterEach, expect, test, vi, beforeEach } from 'vitest'
import { api, apiUpload, orgApi, orgUpload, type ApiError } from './api'

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

test('a 401 with detail "MPIN required" redirects to /mpin, not /login', async () => {
  Object.defineProperty(window, 'location', { value: { href: '/org/1' }, writable: true })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'MPIN required' }), {
      status: 401, headers: { 'content-type': 'application/json' },
    })
  ))
  await expect(api('/api/orgs/1/accounts')).rejects.toThrow('Unauthorized')
  expect(window.location.href).toBe('/mpin')
})

test('401s from the MPIN routes are inline errors, never redirects', async () => {
  Object.defineProperty(window, 'location', { value: { href: '/mpin' }, writable: true })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Invalid MPIN', attempts_left: 3 }), {
      status: 401, headers: { 'content-type': 'application/json' },
    })
  ))
  await expect(api('/api/mpin/verify', { method: 'POST', body: '{}' })).rejects.toThrow('401: Invalid MPIN')
  expect(window.location.href).toBe('/mpin')
})

test('a wrong-current-MPIN 401 from the change-MPIN route is an inline error, never a redirect', async () => {
  const originalLocation = window.location.href
  Object.defineProperty(window, 'location', { value: { href: originalLocation }, writable: true })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Invalid MPIN', attempts_left: 3 }), {
      status: 401, headers: { 'content-type': 'application/json' },
    })
  ))
  await expect(api('/api/me/mpin', { method: 'POST', body: '{}' })).rejects.toThrow('Invalid MPIN')
  expect(window.location.href).toBe(originalLocation)
})

test('a failed response exposes its status and JSON body on the error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'MPIN locked', locked_until: '2026-09-26T10:00:00Z' }), {
      status: 423, headers: { 'content-type': 'application/json' },
    })
  ))
  const err = await api('/api/mpin/verify', { method: 'POST', body: '{}' }).catch((e) => e as ApiError)
  expect(err.response?.status).toBe(423)
  expect(err.response?.body?.locked_until).toBe('2026-09-26T10:00:00Z')
})

test('apiUpload posts FormData with the CSRF header and never sets a Content-Type', async () => {
  document.cookie = 'csrf=tok123'
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: 5, purpose: 'deposit_receipt', content_type: 'image/png', size_bytes: 10 }), {
      status: 201, headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const form = new FormData()
  form.append('purpose', 'deposit_receipt')
  const result = await apiUpload<{ id: number }>('/api/orgs/1/investor/files', form)
  expect(result.id).toBe(5)
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  expect(url).toBe('/api/orgs/1/investor/files')
  expect(init.method).toBe('POST')
  expect(init.body).toBe(form)
  expect(init.credentials).toBe('same-origin')
  const headers = init.headers as Record<string, string>
  expect(headers['X-CSRF-Token']).toBe('tok123')
  expect(headers['Content-Type']).toBeUndefined()
})

test('apiUpload surfaces the server detail with the status prefix', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'file too large (5 MB max)' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    }),
  ))
  await expect(apiUpload('/api/orgs/1/investor/files', new FormData())).rejects.toThrow('400: file too large (5 MB max)')
})

test('orgUpload posts to the org-scoped tail', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  await orgUpload(7, 'investor/files', new FormData())
  expect(fetchMock.mock.calls[0][0]).toBe('/api/orgs/7/investor/files')
})

test('orgApi passes redirectOn401 through so a step-up 401 stays inline', async () => {
  // A wrong MPIN on a step-up POST (withdrawal, transfer, destination,
  // adjustment) answers 401 and must surface as an inline error on the
  // page that asked, never as a bounce to /login (spec section 10).
  Object.defineProperty(window, 'location', {
    value: { href: '/org/1/invest/withdraw' },
    writable: true,
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Invalid MPIN', attempts_left: 3 }), {
      status: 401, headers: { 'content-type': 'application/json' },
    }),
  ))
  await expect(
    orgApi(1, 'investor/withdrawals', { method: 'POST', body: '{}' }, { redirectOn401: false }),
  ).rejects.toThrow('401: Invalid MPIN')
  expect(window.location.href).toBe('/org/1/invest/withdraw')
})
