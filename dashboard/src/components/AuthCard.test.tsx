import { render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import AuthCard from './AuthCard'

afterEach(() => { document.title = 'MirrorFleet' })

test('is the page main region with a glass card, the logo, a real h1, a lead and a footer', () => {
  render(
    <AuthCard title="Sign in" lead="Welcome back." footer={<a href="/register">Create account</a>}>
      <input aria-label="Email" />
    </AuthCard>
  )
  expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByText('Welcome back.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Create account' })).toBeInTheDocument()
  expect(screen.getByLabelText('Email')).toBeInTheDocument()
  expect(screen.getByText('Mirror').closest('.glass')).not.toBeNull()
  expect(document.title).toBe('Sign in · MirrorFleet')
})
