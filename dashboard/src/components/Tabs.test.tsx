import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import Tabs from './Tabs'

const ITEMS = [
  { key: 'a', label: 'Alpha', count: 3 },
  { key: 'b', label: 'Beta' },
  { key: 'c', label: 'Gamma', count: 0 },
]

function Harness({ onChange }: { onChange?: (k: string) => void }) {
  const [value, setValue] = useState('a')
  return (
    <>
      <Tabs items={ITEMS} value={value} onChange={(k) => { setValue(k); onChange?.(k) }} label="History views" idBase="hist" />
      <div id="hist-panel" role="tabpanel" aria-labelledby={`hist-tab-${value}`}>{value}</div>
    </>
  )
}

test('renders an accessible tablist with one selected, focusable tab', () => {
  render(<Harness />)
  const list = screen.getByRole('tablist', { name: 'History views' })
  const tabs = screen.getAllByRole('tab')
  expect(list).toBeInTheDocument()
  expect(tabs).toHaveLength(3)
  expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
  expect(tabs[0]).toHaveAttribute('tabindex', '0')
  expect(tabs[1]).toHaveAttribute('tabindex', '-1')
  expect(tabs[0]).toHaveAttribute('aria-controls', 'hist-panel')
  expect(tabs[0]).toHaveAttribute('id', 'hist-tab-a')
  expect(tabs[0]).toHaveTextContent('3')
})

test('arrow keys, Home and End move selection and focus; the panel follows', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const tabs = screen.getAllByRole('tab')
  tabs[0].focus()
  await userEvent.keyboard('{ArrowRight}')
  expect(tabs[1]).toHaveFocus()
  expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveTextContent('b')
  await userEvent.keyboard('{End}')
  expect(tabs[2]).toHaveFocus()
  await userEvent.keyboard('{ArrowRight}')
  expect(tabs[0]).toHaveFocus()
  await userEvent.keyboard('{Home}')
  expect(tabs[0]).toHaveFocus()
  expect(onChange).toHaveBeenLastCalledWith('a')
})

test('clicking a tab selects it', async () => {
  render(<Harness />)
  await userEvent.click(screen.getByRole('tab', { name: /Gamma/ }))
  expect(screen.getByRole('tab', { name: /Gamma/ })).toHaveAttribute('aria-selected', 'true')
})
