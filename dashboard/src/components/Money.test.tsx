import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test } from 'vitest'
import Money, { HideBalancesToggle } from './Money'
import { setHidden } from '../lib/hideBalances'

// Unmount before resetting the hide-balances store: Vitest runs afterEach
// hooks in reverse order, so RTL's auto-cleanup (registered before this
// file's hooks run) would otherwise fire AFTER setHidden(false) here, while
// the tree is still mounted -- the broadcast then updates Money outside
// act(). Explicit cleanup() first makes the unmount happen before the store
// changes, no matter what order the hooks run in.
afterEach(() => {
  cleanup()
  setHidden(false)
  localStorage.clear()
})

test('renders two-decimal money with its unit in the tabular figure style', () => {
  render(<Money value={5120.5} unit="USD" />)
  const el = screen.getByText('5,120.50 USD')
  expect(el.tagName).toBe('SPAN')
  expect(el).toHaveClass('num')
})

test('a form string renders as money; nothing renders as a dash', () => {
  const { rerender } = render(<Money value="250" />)
  expect(screen.getByText('250.00')).toBeInTheDocument()
  rerender(<Money value={null} unit="USD" />)
  expect(screen.getByText('—')).toBeInTheDocument()
  rerender(<Money value="" unit="USD" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('signed money keeps its sign and unit', () => {
  const { rerender } = render(<Money value={-250} unit="USD" signed />)
  expect(screen.getByText('-250.00 USD')).toBeInTheDocument()
  rerender(<Money value={1000} signed />)
  expect(screen.getByText('+1,000.00')).toBeInTheDocument()
  rerender(<Money value="" signed />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('hidden balances render as dots with an accessible name and no figure', () => {
  setHidden(true)
  render(<Money value={5120.5} unit="USD" className="text-2xl" />)
  const masked = screen.getByRole('img', { name: 'Hidden amount' })
  expect(masked).toHaveTextContent('••••')
  expect(masked).toHaveClass('text-2xl')
  expect(screen.queryByText(/5,120/)).toBeNull()
})

test('a masked figure never carries a profit/loss colour, inherited or passed in', () => {
  setHidden(true)
  // A negative amount's className is normally computed from the real value
  // (e.g. `e.amount < 0 ? 'text-loss' : 'text-profit'`); the wrapper carries
  // the same tone, the way a <dd> around an unsigned Money often does.
  render(
    <div className="text-loss">
      <Money value={-250} unit="USD" signed className="text-loss" />
    </div>,
  )
  const masked = screen.getByRole('img', { name: 'Hidden amount' })
  expect(masked).toHaveClass('text-ink-soft')
  expect(masked.className).not.toMatch(/\btext-(profit|loss)(-deep)?\b/)
})

test('the toggle is a pressed ghost button that flips every Money on the page and persists', async () => {
  render(
    <>
      <HideBalancesToggle />
      <Money value={100} unit="USD" />
      <Money value={200} unit="USD" />
    </>,
  )
  const toggle = screen.getByRole('button', { name: 'Hide balances' })
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()

  await userEvent.click(toggle)
  expect(screen.getByRole('button', { name: 'Show balances' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getAllByRole('img', { name: 'Hidden amount' })).toHaveLength(2)
  expect(screen.queryByText('200.00 USD')).toBeNull()
  expect(localStorage.getItem('mf.hideBalances')).toBe('1')

  await userEvent.click(screen.getByRole('button', { name: 'Show balances' }))
  expect(screen.getByText('200.00 USD')).toBeInTheDocument()
  expect(localStorage.getItem('mf.hideBalances')).toBe('0')
})
