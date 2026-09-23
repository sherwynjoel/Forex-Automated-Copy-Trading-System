// src/pages/Landing.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import Landing from './Landing'

test('the front page offers sign in and account creation and names its sections', () => {
  render(<MemoryRouter><Landing /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/trade once/i)

  const signIns = screen.getAllByRole('link', { name: 'Sign in' })
  expect(signIns.length).toBeGreaterThan(0)
  for (const a of signIns) expect(a).toHaveAttribute('href', '/login')

  const creates = screen.getAllByRole('link', { name: 'Create account' })
  expect(creates.length).toBeGreaterThan(1)
  for (const a of creates) expect(a).toHaveAttribute('href', '/register')

  for (const name of ['Platform', 'How it works', 'For investors', 'FAQ']) {
    expect(screen.getByRole('heading', { name })).toBeInTheDocument()
  }
  expect(screen.getByText(/high level of risk/i)).toBeInTheDocument()
})

test('the page makes no claims it cannot back', () => {
  render(<MemoryRouter><Landing /></MemoryRouter>)
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/regulated|licensed|award|[0-9,]+\+? (clients|traders|users)/i)
})
