import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import NavRail from './NavRail'
import { adminNav, investorNav } from './nav'

test('admin groups are Desk, Fleet and Org, and the current page carries aria-current', () => {
  render(
    <MemoryRouter initialEntries={['/org/7/accounts']}>
      <NavRail groups={adminNav(7, 'admin')} />
    </MemoryRouter>
  )
  expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  for (const g of ['Desk', 'Fleet', 'Org']) expect(screen.getByText(g)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Accounts' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
})

test('a viewer sees no Trade, Automation or Investors; an investor sees the five portal links ungrouped', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'viewer')} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('link', { name: 'Trade' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Automation' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Investors' })).toBeNull()
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <NavRail groups={investorNav(7)} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link')).toHaveLength(5)
  expect(screen.queryByText('Desk')).toBeNull()
  expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
})
