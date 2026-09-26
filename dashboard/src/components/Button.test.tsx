import { createRef } from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import Button from './Button'

test('defaults to a brand primary, type=button, with the touch-height floor', () => {
  render(<Button>Save</Button>)
  const btn = screen.getByRole('button', { name: 'Save' })
  expect(btn).toHaveAttribute('type', 'button')
  expect(btn).toHaveClass('bg-brand', 'text-on-accent', 'hover:bg-brand-deep', 'min-h-11')
})

test('tone changes the fill: profit for Buy, loss for danger, warn for dry-run', () => {
  render(
    <>
      <Button tone="profit">Buy</Button>
      <Button tone="loss">Close all</Button>
      <Button tone="warn">Dry run</Button>
    </>
  )
  expect(screen.getByRole('button', { name: 'Buy' })).toHaveClass('bg-profit', 'hover:bg-profit-deep')
  expect(screen.getByRole('button', { name: 'Close all' })).toHaveClass('bg-loss', 'hover:bg-loss-deep')
  expect(screen.getByRole('button', { name: 'Dry run' })).toHaveClass('bg-warn', 'hover:bg-warn-deep')
})

test('secondary is an outlined control on the card surface; brand tone outlines in violet', () => {
  render(
    <>
      <Button variant="secondary">Cancel</Button>
      <Button variant="secondary" tone="brand">Connect</Button>
    </>
  )
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveClass('border', 'border-field-line', 'text-ink', 'bg-card')
  expect(screen.getByRole('button', { name: 'Connect' })).toHaveClass('border-brand', 'text-brand', 'hover:bg-brand-wash')
})

test('ghost is text-only and still keeps the touch-height floor on phones', () => {
  render(<Button variant="ghost" tone="loss">Remove</Button>)
  const btn = screen.getByRole('button', { name: 'Remove' })
  expect(btn).toHaveClass('text-loss', 'hover:underline', 'min-h-11')
  expect(btn).not.toHaveClass('bg-loss')
})

test('sizes: sm is the compact table-action size, md the default, lg for hero calls to action', () => {
  render(
    <>
      <Button size="sm">Small</Button>
      <Button>Medium</Button>
      <Button size="lg">Large</Button>
    </>
  )
  expect(screen.getByRole('button', { name: 'Small' })).toHaveClass('text-xs', 'px-3', 'py-1.5')
  expect(screen.getByRole('button', { name: 'Medium' })).toHaveClass('text-sm', 'px-4', 'py-2')
  expect(screen.getByRole('button', { name: 'Large' })).toHaveClass('text-base', 'px-6', 'py-3')
})

test('inverse tone keeps the label readable on a solid accent fill', () => {
  render(
    <div className="bg-loss">
      <Button variant="ghost" tone="inverse">Dismiss</Button>
      <Button variant="secondary" tone="inverse">Details</Button>
    </div>
  )
  expect(screen.getByRole('button', { name: 'Dismiss' })).toHaveClass('text-on-accent')
  expect(screen.getByRole('button', { name: 'Dismiss' })).not.toHaveClass('text-ink-soft')
  expect(screen.getByRole('button', { name: 'Details' })).toHaveClass('border-on-accent', 'text-on-accent')
})

test('neutral tone is quiet chrome: ghost reads in soft ink, secondary is the plain outline', () => {
  render(
    <>
      <Button variant="ghost" tone="neutral">Dismiss</Button>
      <Button variant="secondary" tone="neutral">Plain</Button>
    </>
  )
  expect(screen.getByRole('button', { name: 'Dismiss' })).toHaveClass('text-ink-soft', 'hover:text-ink')
  expect(screen.getByRole('button', { name: 'Plain' })).toHaveClass('border-field-line', 'text-ink')
})

test('block stretches to the container', () => {
  render(<Button block>Sign in</Button>)
  expect(screen.getByRole('button', { name: 'Sign in' })).toHaveClass('w-full')
})

test('busy disables the control and announces it', () => {
  render(<Button busy>Working…</Button>)
  const btn = screen.getByRole('button', { name: 'Working…' })
  expect(btn).toBeDisabled()
  expect(btn).toHaveAttribute('aria-busy', 'true')
})

test('type=submit is honoured when asked for', () => {
  render(<Button type="submit">Create</Button>)
  expect(screen.getByRole('button', { name: 'Create' })).toHaveAttribute('type', 'submit')
})

test('to renders a router Link with the same visual recipe', () => {
  render(
    <MemoryRouter>
      <Button to="/org/1/trade" variant="secondary" tone="brand">Open ticket</Button>
    </MemoryRouter>
  )
  const link = screen.getByRole('link', { name: 'Open ticket' })
  expect(link).toHaveAttribute('href', '/org/1/trade')
  expect(link).toHaveClass('border-brand', 'text-brand')
})

test('forwards a ref so dialogs can focus it', () => {
  const ref = createRef<HTMLButtonElement>()
  render(<Button ref={ref}>Focus me</Button>)
  expect(ref.current).toBe(screen.getByRole('button', { name: 'Focus me' }))
})

test('extra className is appended, not replaced', () => {
  render(<Button className="order-3 ml-auto">Later</Button>)
  const btn = screen.getByRole('button', { name: 'Later' })
  expect(btn).toHaveClass('order-3', 'ml-auto', 'bg-brand')
})
