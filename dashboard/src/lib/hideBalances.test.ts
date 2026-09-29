import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { HIDE_KEY, readHidden, setHidden, useHiddenBalances } from './hideBalances'

afterEach(() => {
  setHidden(false)
  localStorage.clear()
})

test('the key is mf.hideBalances and the default is shown', () => {
  expect(HIDE_KEY).toBe('mf.hideBalances')
  expect(readHidden()).toBe(false)
})

test('setHidden persists to localStorage and readHidden reads it back', () => {
  setHidden(true)
  expect(localStorage.getItem('mf.hideBalances')).toBe('1')
  expect(readHidden()).toBe(true)
  setHidden(false)
  expect(localStorage.getItem('mf.hideBalances')).toBe('0')
  expect(readHidden()).toBe(false)
})

test('every mounted hook follows a change made anywhere', () => {
  const a = renderHook(() => useHiddenBalances())
  const b = renderHook(() => useHiddenBalances())
  expect(a.result.current[0]).toBe(false)
  act(() => { b.result.current[1](true) })
  expect(a.result.current[0]).toBe(true)
  expect(b.result.current[0]).toBe(true)
  act(() => { setHidden(false) })
  expect(a.result.current[0]).toBe(false)
})
