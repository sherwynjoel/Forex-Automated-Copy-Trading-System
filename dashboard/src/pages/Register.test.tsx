import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Register from './Register'

afterEach(() => vi.unstubAllGlobals())

function MpinLanding() {
  const [params] = useSearchParams()
  return <div>mpin next {params.get('next')}</div>
}

function renderRegister(path = '/register') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/register" element={<Register />} />
        <Route path="/welcome" element={<div>welcome</div>} />
        <Route path="/mpin" element={<MpinLanding />} />
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

test('posts to /api/register and navigates to /mpin with next=/welcome on success', async () => {
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
    expect(screen.getByText('mpin next /welcome')).toBeInTheDocument()
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

test('the password hint is exposed as an accessible description', () => {
  renderRegister()
  expect(screen.getByLabelText(/^password$/i)).toHaveAccessibleDescription(/10 characters/i)
})

test('with an invite, registration goes to /mpin carrying the join path', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  renderRegister('/register?invite=tok123')

  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/^password$/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))

  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ invite_token: 'tok123' })
  // No join attempt yet: the MPIN comes first, Join finishes the invite afterwards.
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await waitFor(() => {
    expect(screen.getByText('mpin next /join/tok123')).toBeInTheDocument()
  })
})

const CLOSED = 'Self-service registration is disabled; ask an administrator for an invite link'

async function fillAndSubmit() {
  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/^password$/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))
}

test('names the page in its one h1 and in the document title', () => {
  renderRegister()
  expect(screen.getByRole('heading', { level: 1, name: 'Create your account' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(document.title).toBe('Create your account · MirrorFleet')
})

test('closed registration says invite only at once and keeps what was typed', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: CLOSED }), { status: 403 })
  ))
  renderRegister()
  await fillAndSubmit()

  expect(await screen.findByText(/registration is by invite only/i)).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent(/invite only/i)
  // The notice replaces the raw server error; it is not shown twice.
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByLabelText(/display name/i)).toHaveValue('Ada Trader')
  expect(screen.getByLabelText(/email/i)).toHaveValue('ada@example.com')
  expect(screen.getByLabelText(/^password$/i)).toHaveValue('correcthorsebattery')
  expect(screen.queryByText(/mpin next/)).not.toBeInTheDocument()
})

test('closed registration with a stale invite asks for a fresh link', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: CLOSED }), { status: 403 })
  ))
  renderRegister('/register?invite=stale')
  await fillAndSubmit()

  expect(await screen.findByText(/invite link is invalid or expired/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/email/i)).toHaveValue('ada@example.com')
})

test('any other 403 still shows its own detail as an error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'CSRF token missing' }), { status: 403 })
  ))
  renderRegister()
  await fillAndSubmit()

  expect(await screen.findByRole('alert')).toHaveTextContent('CSRF token missing')
  expect(screen.queryByText(/invite only/i)).not.toBeInTheDocument()
})
