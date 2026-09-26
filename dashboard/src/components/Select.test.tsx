import { createRef } from 'react'
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Select from './Select'

test('renders the one select recipe on the card surface so the violet chevron applies', () => {
  render(
    <Select aria-label="Role" defaultValue="viewer">
      <option value="viewer">Viewer</option>
      <option value="admin">Admin</option>
    </Select>
  )
  const select = screen.getByLabelText('Role')
  expect(select).toHaveClass('rounded', 'border', 'border-line-strong', 'bg-card', 'text-ink', 'text-sm')
  expect(select).toHaveValue('viewer')
})

test('block stretches to the container; the default stays inline', () => {
  render(
    <>
      <Select aria-label="Wide" block><option>a</option></Select>
      <Select aria-label="Narrow"><option>a</option></Select>
    </>
  )
  expect(screen.getByLabelText('Wide')).toHaveClass('w-full')
  expect(screen.getByLabelText('Narrow')).not.toHaveClass('w-full')
})

test('forwards the ref and native attributes', () => {
  const ref = createRef<HTMLSelectElement>()
  render(<Select ref={ref} aria-label="Account" disabled><option>a</option></Select>)
  const select = screen.getByLabelText('Account')
  expect(select).toBeDisabled()
  expect(ref.current).toBe(select)
})
