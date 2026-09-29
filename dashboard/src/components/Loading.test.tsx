import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Loading from './Loading'

test('announces as a busy status with skeleton lines and no literal ellipsis text', () => {
  render(<Loading lines={4} />)
  const status = screen.getByRole('status', { name: 'Loading' })
  expect(status).toHaveAttribute('aria-busy', 'true')
  expect(status.querySelectorAll('[data-skeleton]')).toHaveLength(4)
  expect(status).not.toHaveTextContent('...')
})

test('the label is customisable', () => {
  render(<Loading label="Loading positions" />)
  expect(screen.getByRole('status', { name: 'Loading positions' })).toBeInTheDocument()
})

test('the label is also real text inside the status, for readers that skip aria-label on a div', () => {
  render(<Loading label="Loading positions" />)
  const status = screen.getByRole('status', { name: 'Loading positions' })
  expect(status.textContent).toBe('Loading positions')
  expect(status.querySelector('.sr-only')).toHaveTextContent('Loading positions')
})
