import { act, render, screen } from '@testing-library/react'
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

test('Tab closes the menu without selecting', async () => {
  const spy = vi.fn()
  render(<Menu label="Actions" items={items(spy)} />)
  await userEvent.click(screen.getByRole('button', { name: 'Actions' }))
  expect(screen.getByRole('menu')).toBeInTheDocument()
  await userEvent.keyboard('{Tab}')
  expect(screen.queryByRole('menu')).toBeNull()
  expect(spy).not.toHaveBeenCalled()
})

test('clicking outside closes the menu', async () => {
  const spy = vi.fn()
  render(
    <>
      <Menu label="Actions" items={items(spy)} />
      <button>Elsewhere</button>
    </>
  )
  await userEvent.click(screen.getByRole('button', { name: 'Actions' }))
  expect(screen.getByRole('menu')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Elsewhere' }))
  expect(screen.queryByRole('menu')).toBeNull()
  expect(spy).not.toHaveBeenCalled()
})

test('the popover is fixed-position outside any scrolling wrapper, and a scroll closes it', async () => {
  const spy = vi.fn()
  render(
    <div data-testid="scroller" style={{ overflow: 'hidden' }}>
      <Menu label="Actions" items={items(spy)} />
    </div>
  )
  await userEvent.click(screen.getByRole('button', { name: 'Actions' }))
  const menu = screen.getByRole('menu')
  expect(menu.className).toMatch(/\bfixed\b/)
  expect(menu.style.position === 'fixed' || /\bfixed\b/.test(menu.className)).toBe(true)
  // Out of the clipping (and backdrop-filtered) ancestors entirely.
  expect(screen.getByTestId('scroller')).not.toContainElement(menu)
  // Keyboard still works through the portal.
  await userEvent.keyboard('{ArrowDown}')
  expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveFocus()
  act(() => { screen.getByTestId('scroller').dispatchEvent(new Event('scroll')) })
  expect(screen.queryByRole('menu')).toBeNull()
  expect(spy).not.toHaveBeenCalled()
})

test('opens upward when there is no room below the trigger', async () => {
  render(<Menu label="Actions" items={items(vi.fn())} />)
  const trigger = screen.getByRole('button', { name: 'Actions' })
  trigger.getBoundingClientRect = () =>
    ({ top: window.innerHeight - 40, bottom: window.innerHeight - 8, left: 100, right: 140, width: 40, height: 32, x: 100, y: window.innerHeight - 40, toJSON: () => ({}) }) as DOMRect
  await userEvent.click(trigger)
  const menu = screen.getByRole('menu')
  expect(menu.style.bottom).not.toBe('')
  expect(menu.style.top).toBe('')
  expect(menu.style.right).toBe(`${window.innerWidth - 140}px`)
})
