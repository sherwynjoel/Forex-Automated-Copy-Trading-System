import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import PinInput from './PinInput'

function Harness({ onComplete, error, disabled }: {
  onComplete?: (pin: string) => void; error?: string | null; disabled?: boolean
}) {
  const [value, setValue] = useState('')
  return (
    <PinInput id="mpin" label="MPIN" value={value} onChange={setValue}
              onComplete={onComplete} error={error} disabled={disabled} autoFocus />
  )
}

function boxes() {
  // getAllByRole throws on zero matches (unlike queryAllByRole), and
  // type="password" inputs never match role "textbox" -- so the check
  // itself must use queryAllByRole or the masked-box tests can never
  // reach the querySelectorAll fallback.
  return screen.queryAllByRole('textbox', { hidden: true }).length
    ? screen.queryAllByRole('textbox', { hidden: true })
    : Array.from(document.querySelectorAll<HTMLInputElement>('input'))
}

const dots = () => screen.queryAllByText('●')

test('renders six masked single-digit boxes under one label', () => {
  render(<Harness />)
  const inputs = boxes()
  expect(inputs).toHaveLength(6)
  for (const el of inputs) {
    expect(el).toHaveAttribute('type', 'password')
    expect(el).toHaveAttribute('inputmode', 'numeric')
  }
  expect(screen.getByRole('group', { name: 'MPIN' })).toBeInTheDocument()
  expect(inputs[0]).toHaveFocus()
  expect(dots()).toHaveLength(0)
})

test('typing advances, Backspace retreats, non-digits are ignored, six digits complete', async () => {
  const onComplete = vi.fn()
  render(<Harness onComplete={onComplete} />)
  const inputs = boxes()
  await userEvent.keyboard('1a2')
  expect(inputs[0]).toHaveValue('1')
  expect(inputs[1]).toHaveValue('2')
  expect(inputs[2]).toHaveFocus()
  await userEvent.keyboard('{Backspace}')
  expect(inputs[1]).toHaveValue('')
  expect(inputs[1]).toHaveFocus()
  await userEvent.keyboard('23456')
  expect(onComplete).toHaveBeenCalledWith('123456')
  expect(onComplete).toHaveBeenCalledTimes(1)
})

test('each typed digit is masked as a dot until Show reveals it', async () => {
  render(<Harness />)
  await userEvent.keyboard('12')
  expect(dots()).toHaveLength(2)
  expect(screen.queryByText('1')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /show/i }))
  // The dots leave with a short exit animation before the digits stand alone.
  await waitFor(() => expect(dots()).toHaveLength(0))
  expect(screen.getByText('1')).toBeInTheDocument()
  expect(screen.getByText('2')).toBeInTheDocument()
})

test('pasting six digits fills every box and completes', async () => {
  const onComplete = vi.fn()
  render(<Harness onComplete={onComplete} />)
  await userEvent.paste('987654')
  expect(boxes().map((b) => (b as HTMLInputElement).value).join('')).toBe('987654')
  expect(onComplete).toHaveBeenCalledWith('987654')
})

test('Delete clears the digit under the cursor; Home and End move the focus', async () => {
  render(<Harness />)
  const inputs = boxes()
  await userEvent.keyboard('123')
  expect(inputs[3]).toHaveFocus()
  await userEvent.keyboard('{Home}')
  expect(inputs[0]).toHaveFocus()
  await userEvent.keyboard('{End}')
  // The first empty box is where entry continues; End cannot skip past it.
  expect(inputs[3]).toHaveFocus()
  await userEvent.keyboard('{ArrowLeft}{Delete}')
  expect(inputs[2]).toHaveValue('')
  expect(inputs[2]).toHaveFocus()
})

test('Show reveals the digits; the error is announced and linked', async () => {
  render(<Harness error="Wrong MPIN, 3 tries left" />)
  expect(screen.getByRole('alert')).toHaveTextContent('Wrong MPIN, 3 tries left')
  expect(boxes()[0]).toHaveAccessibleDescription(/3 tries left/)
  expect(boxes()[0]).toHaveAttribute('aria-invalid', 'true')
  await userEvent.click(screen.getByRole('button', { name: /show/i }))
  expect(boxes()[0]).toHaveAttribute('type', 'text')
})

test('disabled boxes take no input', async () => {
  const onComplete = vi.fn()
  render(<Harness disabled onComplete={onComplete} />)
  await userEvent.keyboard('123456')
  expect(boxes()[0]).toHaveValue('')
  expect(onComplete).not.toHaveBeenCalled()
})

test('the Show toggle follows disabled and announces its pressed state', async () => {
  const disabledRender = render(<Harness disabled />)
  for (const el of boxes()) expect(el).toBeDisabled()
  expect(screen.getByRole('button', { name: /show/i })).toBeDisabled()
  disabledRender.unmount()

  render(<Harness />)
  const toggle = screen.getByRole('button', { name: /show/i })
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await userEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
  expect(boxes()[0]).toHaveAttribute('type', 'text')
  await userEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(boxes()[0]).toHaveAttribute('type', 'password')
})
