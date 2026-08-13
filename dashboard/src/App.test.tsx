import { render, screen } from '@testing-library/react'
import { expect, test, vi, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import Layout from './components/Layout'

afterEach(() => vi.unstubAllGlobals())

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
