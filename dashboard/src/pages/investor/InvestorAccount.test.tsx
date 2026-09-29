import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks() })

test('the Account page shows who you are, the login forms and its title', () => {
  // AccountSecurity calls useNavigate, so the page needs a router.
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument()
  expect(screen.getByText('Test User')).toBeInTheDocument()
  expect(screen.getByText('user@example.com')).toBeInTheDocument()
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(screen.getByText('Investor')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Change MPIN' })).toBeInTheDocument()
  expect(document.title).toBe('Account · MirrorFleet')
})
