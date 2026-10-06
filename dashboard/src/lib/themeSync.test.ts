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

test('an account that never saved one is seeded from this browser, not reset', async () => {
  localStorage.setItem('mf.theme', 'dark')
  const fetchMock = stubSettings({ theme: 'system', updated_at: null })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
  const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')
  expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({ theme: 'dim' })
})

test('an answer that is not a settings body changes nothing', async () => {
  stubSettings({ copying_enabled: true })
  const choose = vi.fn()
  await syncThemeFromServer(choose)
  expect(choose).not.toHaveBeenCalled()
})
