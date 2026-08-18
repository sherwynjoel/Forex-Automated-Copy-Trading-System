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
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: 7, name: 'Acme', role: 'owner' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetchMock)
  renderWelcome()

  await userEvent.type(screen.getByLabelText(/organization name/i), 'Acme')
  await userEvent.click(screen.getByRole('button', { name: /create organization/i }))

  expect(fetchMock.mock.calls[0][0]).toBe('/api/orgs')
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: 'Acme' })
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
