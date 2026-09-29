import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test } from 'vitest'
import NotFound from './NotFound'

afterEach(() => { document.title = 'MirrorFleet' })

test('names the problem, titles the tab and offers a way back', () => {
  render(<MemoryRouter><NotFound /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'That page is not here' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Back to the desk' })).toHaveAttribute('href', '/')
  expect(document.title).toBe('Page not found · MirrorFleet')
})
