import { createRef } from 'react'
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Input from './Input'

test('renders the one text-field recipe: card surface, strong hairline, ink text, token placeholder', () => {
  render(<Input aria-label="Symbol" placeholder="EURUSD" />)
  const input = screen.getByLabelText('Symbol')
  expect(input).toHaveClass(
    'w-full', 'rounded', 'border', 'border-field-line', 'bg-card', 'text-ink', 'text-sm',
    'placeholder:text-ink-faint',
  )
})

test('num switches the digits to the tabular numeric style', () => {
  render(<Input aria-label="Lots" num />)
  expect(screen.getByLabelText('Lots')).toHaveClass('num')
})

test('invalid paints the loss border and sets aria-invalid', () => {
  render(<Input aria-label="Price" invalid />)
  const input = screen.getByLabelText('Price')
  expect(input).toHaveAttribute('aria-invalid', 'true')
  expect(input).toHaveClass('border-loss')
  expect(input).not.toHaveClass('border-field-line')
})

test('passes native attributes through and forwards the ref', () => {
  const ref = createRef<HTMLInputElement>()
  render(<Input ref={ref} aria-label="Email" type="email" autoComplete="email" required />)
  const input = screen.getByLabelText('Email')
  expect(input).toHaveAttribute('type', 'email')
  expect(input).toHaveAttribute('autocomplete', 'email')
  expect(input).toBeRequired()
  expect(ref.current).toBe(input)
})

test('extra className is appended', () => {
  render(<Input aria-label="Note" className="max-w-xs" />)
  expect(screen.getByLabelText('Note')).toHaveClass('max-w-xs', 'bg-card')
})
