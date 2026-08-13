import { render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from './lib/api'
import Layout from './components/Layout'
import Login from './pages/Login'

afterEach(() => vi.unstubAllGlobals())

// Test component that mimics ProtectedLayout from App.tsx
function ProtectedLayout() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const result = await api<{ authenticated: boolean }>('/api/me')
        setIsAuthenticated(result.authenticated)
      } catch (err) {
        setIsAuthenticated(false)
      }
    }
    checkAuth()
  }, [])

  if (isAuthenticated === null) {
    return <div>Loading...</div>
  }

  if (!isAuthenticated) {
    return <div>Not authenticated</div>
  }

  return <Layout />
}

// Placeholder screen content
const Overview = () => <div>Overview - Coming soon</div>
const Accounts = () => <div>Accounts - Coming soon</div>
const Positions = () => <div>Positions - Coming soon</div>
const Logs = () => <div>Logs - Coming soon</div>

test('Layout renders sidebar navigation', async () => {
  render(
    <MemoryRouter>
      <Layout />
    </MemoryRouter>
  )

  // Sidebar should be visible with title
  expect(screen.getByText(/Forex Dashboard/i)).toBeInTheDocument()

  // Navigation links should be present (check for all matches)
  const overviewLinks = screen.getAllByText(/Overview/i)
  expect(overviewLinks.length).toBeGreaterThan(0)

  expect(screen.getByText(/Accounts/i)).toBeInTheDocument()
  expect(screen.getByText(/Positions/i)).toBeInTheDocument()
  expect(screen.getByText(/Logs/i)).toBeInTheDocument()

  // Logout button should be present
  expect(screen.getByText(/Logout/i)).toBeInTheDocument()
})

test('protected screen content renders through Outlet after auth', async () => {
  // Mock /api/me to return authenticated
  const fetchMock = vi.fn((url: string) => {
    if (url === '/api/me') {
      return Promise.resolve(
        new Response(JSON.stringify({ authenticated: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  // Render full app at root path
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedLayout />}>
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="positions" element={<Positions />} />
          <Route path="logs" element={<Logs />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )

  // Initially loading
  expect(screen.getByText(/Loading/i)).toBeInTheDocument()

  // After auth check, should render Layout with Outlet content
  // This REQUIRES <Outlet/> in Layout to work — will FAIL if Outlet is removed
  await waitFor(() => {
    expect(screen.getByText(/Forex Dashboard/i)).toBeInTheDocument()
    expect(screen.getByText(/Overview - Coming soon/i)).toBeInTheDocument()
  })
})
