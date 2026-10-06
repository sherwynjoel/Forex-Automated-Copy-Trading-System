import { afterEach, expect, test, vi } from 'vitest'
import { isThemePref, localPref, syncThemeFromServer } from './themeSync'

function stubSettings(body: unknown) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(
    JSON.stringify(init?.method === 'PUT' ? JSON.parse(init.body as string) : body),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })

test('isThemePref and localPref', () => {
  expect(isThemePref('dim')).toBe(true)
  expect(isThemePref('neon')).toBe(false)
  expect(localPref()).toBeNull()
  localStorage.setItem('mf.theme', 'dark')
  expect(localPref()).toBe('dim')
  localStorage.setItem('mf.theme', 'light')
  expect(localPref()).toBe('light')
})

test("the account's saved theme is applied", async () => {
  stubSettings({ theme: 'dim', updated_at: '2026-10-05T10:00:00Z' })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).toHaveBeenCalledWith('dim')
})

test('the server never having saved a theme makes no PUT and leaves the local look alone', async () => {
  const fetchMock = stubSettings({ theme: 'system', updated_at: null })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false)
  expect(localStorage.getItem('mf.theme')).toBeNull()
})

test("a shared browser's previous user never gets written onto the next account", async () => {
  localStorage.setItem('mf.theme', 'dark')
  const fetchMock = stubSettings({ theme: 'system', updated_at: null })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false)
  // the local look (this browser's, possibly the PREVIOUS user's choice) is untouched
  expect(localStorage.getItem('mf.theme')).toBe('dark')
})

test('an answer that is not a settings body changes nothing', async () => {
  stubSettings({ copying_enabled: true })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
})
