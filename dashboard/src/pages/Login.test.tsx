import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Login from './Login'

afterEach(() => vi.unstubAllGlobals())

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>home</div>} />
      </Routes>
    </MemoryRouter>
  )
}

test('submits email and password to /api/login and navigates to / on success', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  renderLogin()

  await userEvent.type(screen.getByLabelText(/email/i), 'trader@example.com')
  await userEvent.type(screen.getByLabelText(/password/i), 'hunter2!')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

  expect(fetchMock.mock.calls[0][0]).toBe('/api/login')
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    email: 'trader@example.com',
    password: 'hunter2!',
  })
  await waitFor(() => {
    expect(screen.getByText('home')).toBeInTheDocument()
  })
})

test('displays inline error on login failure (401) without navigating', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response('Unauthorized', { status: 401 }))
  vi.stubGlobal('fetch', fetchMock)
  renderLogin()

  await userEvent.type(screen.getByLabelText(/email/i), 'trader@example.com')
  await userEvent.type(screen.getByLabelText(/password/i), 'wrongpassword')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

  await waitFor(() => {
    expect(screen.getByText(/401/)).toBeInTheDocument()
  })
  expect(screen.queryByText('home')).not.toBeInTheDocument()
})

test('links to the registration page', () => {
  renderLogin()
  expect(screen.getByRole('link', { name: /create an account/i })).toHaveAttribute(
    'href',
    '/register'
  )
})
