import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import SkipLink from './SkipLink'

test('links to #main and is the first focusable thing', () => {
  render(<><SkipLink /><main id="main" tabIndex={-1}>content</main></>)
  const link = screen.getByRole('link', { name: 'Skip to content' })
  expect(link).toHaveAttribute('href', '#main')
  expect(link.className).toContain('sr-only')
  expect(link.className).toContain('focus:not-sr-only')
})
