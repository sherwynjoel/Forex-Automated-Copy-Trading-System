import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { OrgProvider, useOrg, LAST_ORG_KEY } from './org'

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => vi.unstubAllGlobals())

/** Reads the context and renders enough of it to assert its shape. */
function Probe() {
  const { orgId, role, org, me } = useOrg()
  return (
    <div>
      <span>org:{orgId}</span>
      <span>role:{role}</span>
      <span>name:{org.name}</span>
      <span>email:{me.user.email}</span>
    </div>
  )
}

function renderAtOrg(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/org/:orgId"
          element={
            <OrgProvider>
              <Probe />
            </OrgProvider>
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

test('provides org context for a member and persists the last-used org', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        user: { id: 1, email: 'ada@example.com', display_name: 'Ada' },
        orgs: [{ id: 1, name: 'Acme', role: 'admin' }],
      }),
      { status: 200, headers: { 'content-type': 'application/json' } }
    )
  )
  vi.stubGlobal('fetch', fetchMock)

  renderAtOrg('/org/1')

  expect(await screen.findByText('org:1')).toBeInTheDocument()
  expect(screen.getByText('role:admin')).toBeInTheDocument()
  expect(screen.getByText('name:Acme')).toBeInTheDocument()
  expect(screen.getByText('email:ada@example.com')).toBeInTheDocument()

  await waitFor(() => {
    expect(window.localStorage.getItem(LAST_ORG_KEY)).toBe('1')
  })
})

test('redirects to /welcome when the user is not a member of :orgId, without touching lastOrg', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        user: { id: 1, email: 'ada@example.com', display_name: 'Ada' },
        orgs: [{ id: 1, name: 'Acme', role: 'admin' }],
      }),
      { status: 200, headers: { 'content-type': 'application/json' } }
    )
  )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter initialEntries={['/org/999']}>
      <Routes>
        <Route
          path="/org/:orgId"
          element={
            <OrgProvider>
              <Probe />
            </OrgProvider>
          }
        />
        <Route path="/welcome" element={<div>welcome</div>} />
      </Routes>
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('welcome')).toBeInTheDocument()
  })
  expect(window.localStorage.getItem(LAST_ORG_KEY)).toBeNull()
})
