import { Component, lazy, Suspense, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import ChunkBoundary, { isChunkLoadError, markChunkError } from './ChunkBoundary'

// React logs every caught render error; keep the run quiet.
beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

class Outer extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() { return this.state.error ? <p>outer caught {this.state.error.message}</p> : this.props.children }
}

test('a lazy page whose chunk fails to load renders the Reload card', async () => {
  const reload = vi.fn()
  vi.stubGlobal('location', { ...window.location, reload })
  const Page = lazy(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module: /assets/admin-abc.js')))
  render(
    <ChunkBoundary>
      <Suspense fallback={<p>waiting</p>}><Page /></Suspense>
    </ChunkBoundary>,
  )
  expect(await screen.findByText('This page needs a reload after an update.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Reload' }))
  expect(reload).toHaveBeenCalledTimes(1)
})

test('any error the lazy loader marked is treated as a chunk failure, whatever its message', async () => {
  const Page = lazy(() => Promise.reject(markChunkError(new SyntaxError("Unexpected token '<'"))))
  render(
    <ChunkBoundary>
      <Suspense fallback={<p>waiting</p>}><Page /></Suspense>
    </ChunkBoundary>,
  )
  expect(await screen.findByRole('button', { name: 'Reload' })).toBeInTheDocument()
})

test('a non-chunk render error still propagates past the boundary', () => {
  function Broken(): ReactNode { throw new Error('boom') }
  render(
    <Outer>
      <ChunkBoundary><Broken /></ChunkBoundary>
    </Outer>,
  )
  expect(screen.getByText('outer caught boom')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull()
})

test('recognises the browsers’ chunk-failure messages', () => {
  expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: x'))).toBe(true)
  expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
  expect(isChunkLoadError(new Error('Loading chunk 7 failed.'))).toBe(true)
  expect(isChunkLoadError(new Error('boom'))).toBe(false)
  expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
})
