import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi, afterEach } from 'vitest'
import SearchSelect from './SearchSelect'

afterEach(() => {
  vi.clearAllMocks()
})

const options = [
  { value: 'EURUSD', label: 'EURUSD' },
  { value: 'GBPUSD', label: 'GBPUSD' },
  { value: 'XAUUSD', label: 'XAUUSD', keywords: 'gold' },
]

function renderSelect(value = 'EURUSD', onChange = vi.fn()) {
  render(
    <div>
      <label htmlFor="pick">Symbol</label>
      <SearchSelect id="pick" options={options} value={value} onChange={onChange} />
      <button>outside</button>
    </div>
  )
  return onChange
}

test('shows the selected option label in the input, list closed', () => {
  renderSelect('GBPUSD')
  const input = screen.getByRole('combobox', { name: /symbol/i })
  expect(input).toHaveValue('GBPUSD')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

test('opens the full list on click', async () => {
  renderSelect()
  await userEvent.click(screen.getByRole('combobox', { name: /symbol/i }))
  const list = screen.getByRole('listbox')
  expect(screen.getAllByRole('option')).toHaveLength(3)
  expect(list).toBeInTheDocument()
})

test('typing filters options case-insensitively', async () => {
  renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.clear(input)
  await userEvent.type(input, 'gbp')
  const shown = screen.getAllByRole('option')
  expect(shown).toHaveLength(1)
  expect(shown[0]).toHaveTextContent('GBPUSD')
})

test('matches extra keywords, not just the label', async () => {
  renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.clear(input)
  await userEvent.type(input, 'gold')
  const shown = screen.getAllByRole('option')
  expect(shown).toHaveLength(1)
  expect(shown[0]).toHaveTextContent('XAUUSD')
})

test('clicking an option selects it and closes the list', async () => {
  const onChange = renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.click(screen.getByRole('option', { name: 'XAUUSD' }))
  expect(onChange).toHaveBeenCalledWith('XAUUSD')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

test('arrow keys move the highlight and Enter selects', async () => {
  const onChange = renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
  expect(onChange).toHaveBeenCalledWith('GBPUSD')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

test('Escape closes the list and restores the selected label', async () => {
  const onChange = renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.clear(input)
  await userEvent.type(input, 'gbp')
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  expect(input).toHaveValue('EURUSD')
  expect(onChange).not.toHaveBeenCalled()
})

test('clicking outside closes the list without committing typed text', async () => {
  const onChange = renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.clear(input)
  await userEvent.type(input, 'gbp')
  await userEvent.click(screen.getByRole('button', { name: 'outside' }))
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  expect(input).toHaveValue('EURUSD')
  expect(onChange).not.toHaveBeenCalled()
})

test('shows a no-matches message for a filter that hits nothing', async () => {
  renderSelect()
  const input = screen.getByRole('combobox', { name: /symbol/i })
  await userEvent.click(input)
  await userEvent.clear(input)
  await userEvent.type(input, 'zzz')
  expect(screen.queryAllByRole('option')).toHaveLength(0)
  expect(screen.getByText(/no matches/i)).toBeInTheDocument()
})
