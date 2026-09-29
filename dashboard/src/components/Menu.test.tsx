import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import Menu from './Menu'

const items = (spy: (k: string) => void) => [
  { key: 'flatten', label: 'Flatten', onSelect: () => spy('flatten') },
  { key: 'rotate', label: 'Rotate key', disabled: true, onSelect: () => spy('rotate') },
  { key: 'remove', label: 'Remove', tone: 'loss' as const, onSelect: () => spy('remove') },
]

test('opens on click, lists menu items, selects with Enter and returns focus', async () => {
  const spy = vi.fn()
  render(<Menu label="Actions for EUR account" items={items(spy)} />)
  const trigger = screen.getByRole('button', { name: 'Actions for EUR account' })
  expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
  expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(trigger)
  expect(trigger).toHaveAttribute('aria-expanded', 'true')
  const menu = screen.getByRole('menu')
  expect(menu.className).toContain('glass')
  const menuItems = screen.getAllByRole('menuitem')
  expect(menuItems).toHaveLength(3)
  expect(menuItems[0]).toHaveFocus()
  expect(menuItems[1]).toHaveAttribute('aria-disabled', 'true')
  await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
  expect(spy).toHaveBeenCalledWith('remove')
  expect(screen.queryByRole('menu')).toBeNull()
  expect(trigger).toHaveFocus()
})

test('Escape closes without selecting; a disabled item is skipped by arrows', async () => {
  const spy = vi.fn()
  render(<Menu label="Actions" items={items(spy)} />)
  await userEvent.click(screen.getByRole('button', { name: 'Actions' }))
  await userEvent.keyboard('{ArrowDown}')
  expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('menu')).toBeNull()
  expect(spy).not.toHaveBeenCalled()
})
