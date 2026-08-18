import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Register from './Register'

afterEach(() => vi.unstubAllGlobals())

function renderRegister() {
  return render(
    <MemoryRouter initialEntries={['/register']}>
      <Routes>
        <Route path="/register" element={<Register />} />
        <Route path="/welcome" element={<div>welcome</div>} />
      </Routes>
    </MemoryRouter>
  )
}

test('renders display name, email, and password inputs', () => {
  renderRegister()
  expect(screen.getByLabelText(/display name/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
})

test('posts to /api/register and navigates to /welcome on success', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  renderRegister()

  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/password/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))

  expect(fetchMock.mock.calls[0][0]).toBe('/api/register')
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    email: 'ada@example.com',
    password: 'correcthorsebattery',
    display_name: 'Ada Trader',
  })
  await waitFor(() => {
    expect(screen.getByText('welcome')).toBeInTheDocument()
  })
})

test('shows the server detail on 409 without navigating', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Email already registered' }), { status: 409 })
  )
  vi.stubGlobal('fetch', fetchMock)
  renderRegister()

  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/password/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))

  await waitFor(() => {
    expect(screen.getByText('Email already registered')).toBeInTheDocument()
  })
  expect(screen.queryByText(/409/)).not.toBeInTheDocument()
  expect(screen.queryByText('welcome')).not.toBeInTheDocument()
})

test('links back to login', () => {
  renderRegister()
  expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login')
})
