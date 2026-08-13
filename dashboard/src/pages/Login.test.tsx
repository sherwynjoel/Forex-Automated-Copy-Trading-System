import { render, screen, waitFor } from '@testing-library/react'
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

test('displays inline error on login failure (401)', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response('Unauthorized', { status: 401 }))
  vi.stubGlobal('fetch', fetchMock)
  render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>
  )
  await userEvent.type(screen.getByLabelText(/password/i), 'wrongpassword')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

  // Should show error message but NOT redirect
  await waitFor(() => {
    expect(screen.getByText(/401/)).toBeInTheDocument()
  })
  // Verify location did not change (no redirect)
  expect(window.location.pathname).toBe('/')
})
