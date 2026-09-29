import { render, screen, within } from '@testing-library/react'
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
  expect(screen.getByRole('link', { name: 'Requests' })).toHaveAttribute('href', '/org/7/requests')
})

test('a viewer sees no Trade, Automation, Investors or Requests; an investor sees nine portal links in Money and Trading groups', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'viewer')} />
    </MemoryRouter>
  )
  for (const name of ['Trade', 'Automation', 'Investors', 'Requests']) {
    expect(screen.queryByRole('link', { name })).toBeNull()
  }
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <NavRail groups={investorNav(7)} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link')).toHaveLength(9)
  expect(screen.getByText('Money')).toBeInTheDocument()
  expect(screen.getByText('Trading')).toBeInTheDocument()
  expect(screen.queryByText('Desk')).toBeNull()
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'Wallet' })).not.toHaveAttribute('aria-current')
  expect(screen.getByRole('link', { name: 'Payout accounts' })).toHaveAttribute('href', '/org/7/invest/payout-accounts')
})

test('the Requests link carries its open count as a neutral pill and says so in its name', () => {
  render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin', 3)} />
    </MemoryRouter>
  )
  const link = screen.getByRole('link', { name: 'Requests, 3 open' })
  expect(link).toHaveAttribute('href', '/org/7/requests')
  const pill = within(link).getByText('3')
  expect(pill.className).toContain('bg-line')
  expect(pill.className).toContain('rounded-full')
})

test('a zero or unknown count shows no pill and leaves the name alone', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin', 0)} />
    </MemoryRouter>
  )
  expect(screen.getByRole('link', { name: 'Requests' })).toBeInTheDocument()
  expect(screen.queryByText('0')).toBeNull()
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin')} />
    </MemoryRouter>
  )
  expect(screen.getByRole('link', { name: 'Requests' })).toBeInTheDocument()
})
