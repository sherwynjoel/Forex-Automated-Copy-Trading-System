import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Settings from './Settings'
import { mockUseOrg } from '../test/orgMock'
import { TOPIC_EMAIL_LABELS } from '../lib/engagement'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { theme?: string; refusePrefs?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url === '/api/me/settings' && method === 'PUT') {
      return jsonResponse({ ...JSON.parse(init!.body as string), updated_at: '2026-10-05T10:00:00Z' })
    }
    if (url === '/api/me/settings') return jsonResponse({ theme: opts.theme ?? 'system', updated_at: null })
    if (url.endsWith('/notification-prefs') && method === 'PUT') {
      if (opts.refusePrefs) return jsonResponse({ detail: 'database unavailable' }, 500)
      return jsonResponse(JSON.parse(init!.body as string))
    }
    if (url.endsWith('/notification-prefs')) {
      return jsonResponse({ money: true, identity: true, support: true, bonus: true })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).endsWith(fragment) && (init as RequestInit | undefined)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear()
  delete document.documentElement.dataset.theme
})

test('shows the account theme and saves a new one, painting it at once', async () => {
  const fetchMock = mockRoutes({ theme: 'dark' })
  render(<MemoryRouter><Settings /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
  expect(await screen.findByRole('radio', { name: 'Dim' })).toBeChecked()   // the server's dark reads as Dim
  await userEvent.click(screen.getByRole('radio', { name: 'Light' }))
  expect(document.documentElement.dataset.theme).toBe('light')
  await waitFor(() => expect(bodyOf(fetchMock, '/api/me/settings', 'PUT')).toEqual({ theme: 'light' }))
  expect(await screen.findByText('Appearance saved')).toBeInTheDocument()
})

test('two quick theme picks leave only the latest in control: a stale success never overwrites a later error', async () => {
  let releaseFirst: ((r: Response) => void) | null = null
  let putCount = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url === '/api/me/settings' && method === 'PUT') {
      putCount += 1
      if (putCount === 1) {
        // The first (Dim) pick's PUT is held: it will resolve AFTER the second.
        return new Promise<Response>((resolve) => { releaseFirst = resolve })
      }
      return jsonResponse({ detail: 'database unavailable' }, 500)
    }
    if (url === '/api/me/settings') return jsonResponse({ theme: 'light', updated_at: '2026-10-05T09:00:00Z' })
    if (url.endsWith('/notification-prefs')) {
      return jsonResponse({ money: true, identity: true, support: true, bonus: true })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><Settings /></MemoryRouter>)
  expect(await screen.findByRole('radio', { name: 'Light' })).toBeChecked()

  await userEvent.click(screen.getByRole('radio', { name: 'Dim' }))
  await userEvent.click(screen.getByRole('radio', { name: 'System' }))

  // The second (latest) pick's PUT failed immediately: its error wins.
  expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()

  // The first (superseded) pick's PUT now resolves successfully, late. It
  // must not resurrect "Appearance saved" over the later, still-current error.
  await act(async () => { releaseFirst!(jsonResponse({ theme: 'dim', updated_at: '2026-10-05T10:00:00Z' })) })
  expect(screen.queryByText('Appearance saved')).not.toBeInTheDocument()
  expect(screen.getByText('database unavailable')).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()
})

test('the four email switches save the whole set', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Settings /></MemoryRouter>)
  const money = await screen.findByRole('switch', { name: TOPIC_EMAIL_LABELS.money })
  expect(money).toBeChecked()
  await userEvent.click(money)
  await waitFor(() => expect(bodyOf(fetchMock, '/notification-prefs', 'PUT'))
    .toEqual({ money: false, identity: true, support: true, bonus: true }))
  expect(await screen.findByText('Email preferences saved')).toBeInTheDocument()
  expect(money).not.toBeChecked()
})

test('a refused save puts the switch back and says why', async () => {
  mockRoutes({ refusePrefs: true })
  render(<MemoryRouter><Settings /></MemoryRouter>)
  const bonus = await screen.findByRole('switch', { name: TOPIC_EMAIL_LABELS.bonus })
  await userEvent.click(bonus)
  expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  expect(bonus).toBeChecked()
})
