import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Badge from './Badge'

test('each tone pairs its wash background with its deep text (the pairs the palette prover gates)', () => {
  render(
    <>
      <Badge tone="profit">ok</Badge>
      <Badge tone="loss">degraded</Badge>
      <Badge tone="warn">DRY RUN</Badge>
      <Badge tone="brand">manual</Badge>
      <Badge tone="neutral">paused</Badge>
    </>
  )
  expect(screen.getByText('ok')).toHaveClass('bg-profit-wash', 'text-profit-deep')
  expect(screen.getByText('degraded')).toHaveClass('bg-loss-wash', 'text-loss-deep')
  expect(screen.getByText('DRY RUN')).toHaveClass('bg-warn-wash', 'text-warn-deep')
  expect(screen.getByText('manual')).toHaveClass('bg-brand-wash', 'text-brand-deep')
  expect(screen.getByText('paused')).toHaveClass('bg-line', 'text-ink-soft')
})

test('is an inline chip: small, semibold, rounded', () => {
  render(<Badge tone="warn">DRY RUN</Badge>)
  expect(screen.getByText('DRY RUN')).toHaveClass('inline-flex', 'rounded', 'text-xs', 'font-semibold')
})

test('pill rounds fully for status pills', () => {
  render(<Badge tone="warn" pill>DRY RUN</Badge>)
  expect(screen.getByText('DRY RUN')).toHaveClass('rounded-full')
})

test('title passes through for chips that abbreviate a longer message', () => {
  render(<Badge tone="loss" title="Send failed: connection reset">send failed</Badge>)
  expect(screen.getByText('send failed')).toHaveAttribute('title', 'Send failed: connection reset')
})

test('extra className is appended', () => {
  render(<Badge tone="neutral" className="ml-2">x</Badge>)
  expect(screen.getByText('x')).toHaveClass('ml-2', 'bg-line')
})
