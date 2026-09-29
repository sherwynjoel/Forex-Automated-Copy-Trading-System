import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import BottomBar from './BottomBar'

test('admins get four links and More; More opens the menu', async () => {
  const onMore = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/7/positions']}>
      <BottomBar orgId={7} role="admin" onMore={onMore} />
    </MemoryRouter>
  )
  const nav = screen.getByRole('navigation', { name: 'Quick navigation' })
  expect(nav.className).toContain('glass')
  // Below drawers (z-40) and dialogs (z-50), so an open drawer covers it.
  expect(nav.className).toContain('z-30')
  expect(nav.className).not.toContain('z-40')
  const links = screen.getAllByRole('link')
  expect(links.map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'Trade', 'Accounts'])
  expect(screen.getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
  await userEvent.click(screen.getByRole('button', { name: 'More' }))
  expect(onMore).toHaveBeenCalledTimes(1)
})

test('viewers get History instead of Trade; investors get Dashboard, Deposit, Withdraw, Transactions and More too', async () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <BottomBar orgId={7} role="viewer" onMore={() => {}} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'History', 'Accounts'])
  unmount()
  const onMore = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <BottomBar orgId={7} role="investor" onMore={onMore} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Dashboard', 'Deposit', 'Withdraw', 'Transactions'])
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
  await userEvent.click(screen.getByRole('button', { name: 'More' }))
  expect(onMore).toHaveBeenCalledTimes(1)
})
