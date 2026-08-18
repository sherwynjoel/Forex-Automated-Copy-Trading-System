import { render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import App from './App'

beforeEach(() => {
  window.localStorage.clear()
  window.history.pushState({}, '', '/')
})

afterEach(() => vi.unstubAllGlobals())

function meResponse(orgs: Array<{ id: number; name: string; role: string }>) {
  return new Response(
    JSON.stringify({ user: { id: 1, email: 'a@example.com', display_name: 'A' }, orgs }),
    { status: 200, headers: { 'content-type': 'application/json' } }
  )
}

test('Layout renders sidebar navigation', async () => {
  // Layout now resolves the org from route context (OrgProvider), so this
  // renders the full App at an org route rather than a bare <Layout/>.
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') {
      return Promise.resolve(meResponse([{ id: 1, name: 'Acme', role: 'owner' }]))
    }
    return Promise.resolve(
      new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  window.history.pushState({}, '', '/org/1')

  render(<App />)

  // Sidebar should be visible with title
  expect(await screen.findByText(/Copy Desk/i)).toBeInTheDocument()

  // Navigation links should be present (check for all matches)
  const overviewLinks = screen.getAllByText(/Overview/i)
  expect(overviewLinks.length).toBeGreaterThan(0)

  expect(screen.getByRole('link', { name: 'Accounts' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Positions' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Trade' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'History' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Logs' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Members' })).toBeInTheDocument()

  // Logout button should be present
  expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
})

test('/ redirects to /welcome when the user has no orgs', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') return Promise.resolve(meResponse([]))
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)

  await waitFor(() => {
    expect(window.location.pathname).toBe('/welcome')
  })
  expect(await screen.findByText(/create an organization/i)).toBeInTheDocument()
})

test('an /org/:orgId route redirects to /welcome when the user is not a member of that org', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') {
      return Promise.resolve(meResponse([{ id: 5, name: 'Acme', role: 'admin' }]))
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  window.history.pushState({}, '', '/org/999')

  render(<App />)

  await waitFor(() => {
    expect(window.location.pathname).toBe('/welcome')
  })
})
