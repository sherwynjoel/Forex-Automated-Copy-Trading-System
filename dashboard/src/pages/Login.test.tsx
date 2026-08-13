import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Login from './Login'

afterEach(() => vi.unstubAllGlobals())

test('submits password to /api/login', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>
  )
  await userEvent.type(screen.getByLabelText(/password/i), 'hunter2!')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
  expect(fetchMock.mock.calls[0][0]).toBe('/api/login')
})
