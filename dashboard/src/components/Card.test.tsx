import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Card from './Card'

test('renders a glass section with an optional titled header and actions', () => {
  render(<Card title="Fleet" actions={<button>Refresh</button>}><p>body</p></Card>)
  const section = screen.getByRole('region', { name: 'Fleet' })
  expect(section.className).toContain('glass')
  expect(section.className).toContain('rounded-card')
  expect(screen.getByRole('heading', { level: 2, name: 'Fleet' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})

test('inset puts the children on a crisp data surface', () => {
  render(<Card inset><table><tbody><tr><td>1.00</td></tr></tbody></table></Card>)
  expect(screen.getByText('1.00').closest('.inset')).not.toBeNull()
})

test('without a title there is no heading and no region name', () => {
  render(<Card><p>quiet</p></Card>)
  expect(screen.queryByRole('heading')).toBeNull()
  expect(screen.getByText('quiet')).toBeInTheDocument()
})
