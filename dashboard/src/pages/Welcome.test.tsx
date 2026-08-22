import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Welcome from './Welcome'

afterEach(() => vi.unstubAllGlobals())

function JoinLanding() {
  const { token } = useParams()
  return <div>join page {token}</div>
}

function renderWelcome() {
  return render(
    <MemoryRouter initialEntries={['/welcome']}>
      <Routes>
        <Route path="/welcome" element={<Welcome />} />
        <Route path="/org/:orgId" element={<div>org home</div>} />
        <Route path="/join/:token" element={<JoinLanding />} />
      </Routes>
    </MemoryRouter>
  )
}

test('creates an organization and navigates to it', async () => {
  // A fresh Response per call: a body can only be read once, so a single
  // shared instance is consumed by the first request and empty for the next.
  const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
    String(input) === '/api/me'
      ? new Response(JSON.stringify({
          user: { id: 1, email: 'a@example.com', display_name: 'A' }, orgs: [],
        }), { status: 200, headers: { 'content-type': 'application/json' } })
      : new Response(JSON.stringify({ id: 7, name: 'Acme', role: 'owner' }), {
          status: 201, headers: { 'content-type': 'application/json' },
        }))
  vi.stubGlobal('fetch', fetchMock)
  renderWelcome()

  await userEvent.type(screen.getByLabelText(/organization name/i), 'Acme')
  await userEvent.click(screen.getByRole('button', { name: /create organization/i }))

  // Find the create call rather than assuming it is first: the page also
  // asks /api/me on mount to list workspaces you already belong to.
  const create = fetchMock.mock.calls.find(([u]) => String(u) === '/api/orgs')
  expect(create).toBeTruthy()
  expect(JSON.parse(create[1].body)).toEqual({ name: 'Acme' })
  await waitFor(() => {
    expect(screen.getByText('org home')).toBeInTheDocument()
  })
})

test('navigates to the join page with a bare invite code', async () => {
  renderWelcome()

  await userEvent.type(screen.getByLabelText(/invite link or code/i), 'tok_123')
  await userEvent.click(screen.getByRole('button', { name: /join organization/i }))

  await waitFor(() => {
    expect(screen.getByText('join page tok_123')).toBeInTheDocument()
  })
})

test('extracts the token from a full invite URL', async () => {
  renderWelcome()

  await userEvent.type(
    screen.getByLabelText(/invite link or code/i),
    'https://desk.example.com/join/tok_456?utm=email'
  )
  await userEvent.click(screen.getByRole('button', { name: /join organization/i }))

  await waitFor(() => {
    expect(screen.getByText('join page tok_456')).toBeInTheDocument()
  })
})

test('an existing member is shown a way back into their workspaces', async () => {
  // Everything that lands here -- leaving an org, an invite already
  // accepted -- used to offer only "create" and "join", stranding a member
  // who simply wanted to open the org they are already in.
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/me') {
      return new Response(JSON.stringify({
        user: { id: 1, email: 'a@example.com', display_name: 'A' },
        orgs: [{ id: 7, name: 'TheArkTech', role: 'owner' }],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  }))

  render(<MemoryRouter><Welcome /></MemoryRouter>)

  const link = await screen.findByRole('link', { name: /thearktech/i })
  expect(link).toHaveAttribute('href', '/org/7')
})

test('a brand-new account sees no workspace list', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/me') {
      return new Response(JSON.stringify({
        user: { id: 2, email: 'new@example.com', display_name: 'N' }, orgs: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  }))

  render(<MemoryRouter><Welcome /></MemoryRouter>)

  expect(await screen.findByText(/create an organization/i)).toBeInTheDocument()
  expect(screen.queryByText(/your workspaces/i)).not.toBeInTheDocument()
})
