import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import ConfirmDialog from './ConfirmDialog'

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  render(
    <ConfirmDialog
      open
      title="Close position 42"
      confirmLabel="Close position"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    >
      <p>This closes at market.</p>
    </ConfirmDialog>
  )
  return { onConfirm, onCancel }
}

test('Escape cancels the dialog', async () => {
  const { onCancel, onConfirm } = renderDialog()
  await userEvent.keyboard('{Escape}')
  expect(onCancel).toHaveBeenCalled()
  expect(onConfirm).not.toHaveBeenCalled()
})

test('opening moves focus into the dialog (Cancel first, so Enter cannot confirm blind)', () => {
  renderDialog()
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
})

test('Tab is trapped inside the dialog', async () => {
  renderDialog()
  // Cancel -> confirm -> wraps back to Cancel, never the page behind.
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Close position' })).toHaveFocus()
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
})

test('closing returns focus to the control that opened it', async () => {
  function Harness() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button onClick={() => setOpen(true)}>Open dialog</button>
        <ConfirmDialog
          open={open}
          title="Cancel order 9"
          confirmLabel="Cancel this order"
          onConfirm={() => {}}
          onCancel={() => setOpen(false)}
        >
          <p>Nothing is closed.</p>
        </ConfirmDialog>
      </>
    )
  }
  render(<Harness />)
  const trigger = screen.getByRole('button', { name: 'Open dialog' })
  await userEvent.click(trigger)
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(trigger).toHaveFocus()
})
