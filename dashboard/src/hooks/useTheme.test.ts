import { renderHook, act } from '@testing-library/react'
import { expect, test, afterEach, vi } from 'vitest'
import { useTheme } from './useTheme'

afterEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
  vi.unstubAllGlobals()
  document.querySelector('meta[name="theme-color"]')?.remove()
})

type MqListener = (e: { matches: boolean }) => void

/** matchMedia stub for jsdom: fixed `matches`, captures change listeners. */
function stubMatchMedia(matches: boolean): { fire: (matches: boolean) => void } {
  const listeners: MqListener[] = []
  vi.stubGlobal('matchMedia', () => ({
    matches,
    addEventListener: (_: string, cb: MqListener) => listeners.push(cb),
    removeEventListener: (_: string, cb: MqListener) => {
      const i = listeners.indexOf(cb)
      if (i >= 0) listeners.splice(i, 1)
    },
  }))
  return { fire: (m: boolean) => listeners.forEach((cb) => cb({ matches: m })) }
}

test('defaults to light when nothing is stored and the OS gives no signal', () => {
  const { result } = renderHook(() => useTheme())
  expect(result.current.theme).toBe('light')
  expect(document.documentElement.dataset.theme).toBe('light')
})

test('honors a stored choice on load', () => {
  localStorage.setItem('mf.theme', 'dark')
  const { result } = renderHook(() => useTheme())
  expect(result.current.theme).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
})

test('toggling flips the document theme and remembers it', () => {
  const { result } = renderHook(() => useTheme())
  act(() => {
    result.current.toggle()
  })
  expect(result.current.theme).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(localStorage.getItem('mf.theme')).toBe('dark')

  act(() => {
    result.current.toggle()
  })
  expect(result.current.theme).toBe('light')
  expect(localStorage.getItem('mf.theme')).toBe('light')
})

test('follows an OS dark preference on first visit', () => {
  stubMatchMedia(true)
  const { result } = renderHook(() => useTheme())
  expect(result.current.theme).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
})

test('an OS theme change is followed while no explicit choice is stored', () => {
  const mq = stubMatchMedia(false)
  const { result } = renderHook(() => useTheme())
  expect(result.current.theme).toBe('light')
  act(() => {
    mq.fire(true)
  })
  expect(result.current.theme).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
})

test('an OS theme change is ignored once the user chose explicitly', () => {
  const mq = stubMatchMedia(false)
  localStorage.setItem('mf.theme', 'light')
  const { result } = renderHook(() => useTheme())
  act(() => {
    mq.fire(true)
  })
  expect(result.current.theme).toBe('light')
  expect(document.documentElement.dataset.theme).toBe('light')
})

test('a toggle in another tab syncs through the storage event', () => {
  const { result } = renderHook(() => useTheme())
  expect(result.current.theme).toBe('light')
  act(() => {
    window.dispatchEvent(new StorageEvent('storage', { key: 'mf.theme', newValue: 'dark' }))
  })
  expect(result.current.theme).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')
})

test('toggling keeps the browser-chrome theme-color meta in step', () => {
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'theme-color')
  meta.setAttribute('content', '#f8f8fa')
  document.head.appendChild(meta)
  const { result } = renderHook(() => useTheme())
  act(() => {
    result.current.toggle()
  })
  expect(meta.getAttribute('content')).toBe('#131118')
  act(() => {
    result.current.toggle()
  })
  expect(meta.getAttribute('content')).toBe('#f8f8fa')
})
