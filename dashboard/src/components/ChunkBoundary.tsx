import { Component, type ReactNode } from 'react'
import Button from './Button'
import Card from './Card'

const CHUNK_FAILURE = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Loading chunk/i
const MARK = Symbol.for('mirrorfleet.chunkLoadError')

/** Tag an error thrown while loading a lazy route's chunk, so the boundary
 *  treats it as one even when the browser words it differently (a stale
 *  asset answered with index.html can surface as a SyntaxError). */
export function markChunkError<E>(error: E): E {
  if (error && typeof error === 'object') (error as Record<symbol, unknown>)[MARK] = true
  return error
}

export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  return (error as unknown as Record<symbol, unknown>)[MARK] === true || CHUNK_FAILURE.test(error.message)
}

/**
 * Catches a lazy route whose chunk no longer exists (a tab left open across
 * a deploy: the api answers the old /assets/*.js with index.html) and offers
 * a reload instead of a white screen. Any other error is re-thrown to the
 * next boundary up, untouched.
 */
export default class ChunkBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  render() {
    const { error } = this.state
    if (error == null) return this.props.children
    if (!isChunkLoadError(error)) throw error
    return (
      <Card className="mx-auto max-w-md">
        <p role="alert" className="text-sm text-ink">This page needs a reload after an update.</p>
        <Button className="mt-4" onClick={() => location.reload()}>Reload</Button>
      </Card>
    )
  }
}
