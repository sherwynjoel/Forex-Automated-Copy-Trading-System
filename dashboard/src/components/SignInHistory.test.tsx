import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import SignInHistory from './SignInHistory'
import { signInFixture } from '../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists sign-ins with the IP, the device and the outcome', async () => {
  const fetchMock = vi.fn(async () => jsonResponse([
    signInFixture({ id: 2, ip: '10.0.0.9', outcome: 'failed' }), signInFixture()]))
  vi.stubGlobal('fetch', fetchMock)
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(screen.getByRole('status', { name: 'Loading sign-ins' })).toBeInTheDocument()
  expect(await screen.findByText('10.0.0.9')).toBeInTheDocument()
  expect(screen.getByText('Wrong password')).toBeInTheDocument()
  expect(screen.getByText('Signed in')).toBeInTheDocument()
  expect(screen.getAllByText('Chrome on Windows')).toHaveLength(2)
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/me/sign-ins?limit=50')
})

test('an empty history says so', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([])))
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(await screen.findByText('No sign-ins recorded yet')).toBeInTheDocument()
})

test('a failed load shows why', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'database unavailable' }, 500)))
  render(<SignInHistory path="/api/me/sign-ins?limit=50" />)
  expect(await screen.findByText('database unavailable')).toBeInTheDocument()
})
