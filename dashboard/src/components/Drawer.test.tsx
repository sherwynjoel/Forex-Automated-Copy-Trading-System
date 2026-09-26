import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import Drawer from './Drawer'

function renderDrawer(overrides: Partial<React.ComponentProps<typeof Drawer>> = {}) {
  const onClose = vi.fn()
  render(
    <Drawer open title="Account 12345" onClose={onClose} {...overrides}>
      <p>Balance 1,000.00</p>
      <button>Flatten</button>
    </Drawer>
  )
  return { onClose }
}

test('renders nothing while closed', () => {
  renderDrawer({ open: false })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('is a labelled modal dialog: glass header, opaque body for the data', () => {
  renderDrawer()
  const dialog = screen.getByRole('dialog', { name: 'Account 12345' })
  expect(dialog).toHaveAttribute('aria-modal', 'true')
  expect(dialog).toHaveClass('bg-card')
  expect(dialog).not.toHaveClass('glass')
  const heading = screen.getByRole('heading', { name: 'Account 12345' })
  expect(heading.closest('.glass')).not.toBeNull()
})

test('opening moves focus to the Close control', () => {
  renderDrawer()
  expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
})

test('Escape closes; the backdrop click closes too', async () => {
  const { onClose } = renderDrawer()
  await userEvent.keyboard('{Escape}')
  expect(onClose).toHaveBeenCalledTimes(1)
  await userEvent.click(screen.getByTestId('drawer-backdrop'))
  expect(onClose).toHaveBeenCalledTimes(2)
})

test('Tab is trapped inside the panel and wraps in both directions', async () => {
  renderDrawer()
  const close = screen.getByRole('button', { name: 'Close' })
  const flatten = screen.getByRole('button', { name: 'Flatten' })
  expect(close).toHaveFocus()
  await userEvent.tab()
  expect(flatten).toHaveFocus()
  await userEvent.tab()
  expect(close).toHaveFocus()
  await userEvent.tab({ shift: true })
  expect(flatten).toHaveFocus()
})

test('closing returns focus to the control that opened it', async () => {
  function Harness() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button onClick={() => setOpen(true)}>Details</button>
        <Drawer open={open} title="Account 7" onClose={() => setOpen(false)}>
          <p>x</p>
        </Drawer>
      </>
    )
  }
  render(<Harness />)
  const opener = screen.getByRole('button', { name: 'Details' })
  await userEvent.click(opener)
  expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
})

test('an optional header slot renders beside the title', () => {
  renderDrawer({ headerExtra: <span>MT5</span> })
  expect(screen.getByText('MT5')).toBeInTheDocument()
})

test('a ConfirmDialog stacked on a Drawer owns the keyboard: Escape closes only the dialog, Tab stays inside it', async () => {
  const ConfirmDialog = (await import('./ConfirmDialog')).default
  function Harness() {
    const [drawer, setDrawer] = useState(true)
    const [dialog, setDialog] = useState(false)
    return (
      <Drawer open={drawer} title="Account 7" onClose={() => setDrawer(false)}>
        <button onClick={() => setDialog(true)}>Rotate key</button>
        <ConfirmDialog
          open={dialog}
          title="Rotate the key?"
          confirmLabel="Rotate"
          onConfirm={() => setDialog(false)}
          onCancel={() => setDialog(false)}
        >
          <p>The old key stops working.</p>
        </ConfirmDialog>
      </Drawer>
    )
  }
  render(<Harness />)
  await userEvent.click(screen.getByRole('button', { name: 'Rotate key' }))
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  // Tab wraps inside the dialog, never back to the drawer's Close.
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Rotate' })).toHaveFocus()
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  // Escape closes the dialog only; the drawer is still open and focus returns to its opener.
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: 'Rotate the key?' })).not.toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: 'Account 7' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Rotate key' })).toHaveFocus()
})

test('the Close control honours busy', () => {
  renderDrawer({ busy: true })
  expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled()
})
