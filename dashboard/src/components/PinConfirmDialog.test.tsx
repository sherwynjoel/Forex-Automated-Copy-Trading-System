import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import PinConfirmDialog, { mpinErrorText } from './PinConfirmDialog'
import type { ApiError } from '../lib/api'

function apiError(status: number, body: Record<string, unknown>, detail = 'refused'): ApiError {
  const err = new Error(`${status}: ${detail}`) as ApiError
  err.response = { status, body }
  return err
}

function renderDialog(onConfirm: (mpin: string) => Promise<void>, open = true) {
  const onCancel = vi.fn()
  const view = render(
    <PinConfirmDialog open={open} title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}>
      <p>Fee 2.50 USD, you receive 247.50 USD.</p>
    </PinConfirmDialog>,
  )
  return { onCancel, ...view }
}

async function typePin(pin: string) {
  await userEvent.click(screen.getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

const boxes = () => screen.getAllByLabelText(/Your MPIN digit \d of 6/) as HTMLInputElement[]

test('the summary and the MPIN boxes sit inside the dialog; confirm waits for six digits, then passes the MPIN', async () => {
  const onConfirm = vi.fn(async () => {})
  renderDialog(onConfirm)
  const dialog = screen.getByRole('dialog', { name: 'Send 250.00 USD to ICICI Bank ••4543?' })
  expect(dialog).toHaveTextContent('Fee 2.50 USD, you receive 247.50 USD.')
  expect(boxes()).toHaveLength(6)
  const confirm = screen.getByRole('button', { name: 'Send 250.00 USD' })
  expect(confirm).toBeDisabled()

  await typePin('12345')
  expect(confirm).toBeDisabled()
  await userEvent.keyboard('6')
  expect(confirm).toBeEnabled()

  await userEvent.click(confirm)
  await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('123456'))
  expect(onConfirm).toHaveBeenCalledTimes(1)
})

test('a wrong MPIN names the tries left, clears the boxes and keeps the dialog open', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { detail: 'Invalid MPIN', attempts_left: 2 }, 'Invalid MPIN'))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 2 tries left')
  expect(boxes().map((b) => b.value).join('')).toBe('')
  expect(screen.getByRole('button', { name: 'Send 250.00 USD' })).toBeDisabled()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('the last try is singular', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { attempts_left: 1 }))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 1 try left')
})

test('a locked MPIN says about how many minutes to wait', async () => {
  const until = new Date(Date.now() + 14 * 60_000 - 5_000).toISOString()
  const onConfirm = vi.fn().mockRejectedValue(apiError(423, { detail: 'MPIN locked', locked_until: until }, 'MPIN locked'))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('MPIN locked. Try again in about 14 minutes')
})

test('an unset MPIN and any other refusal read plainly', async () => {
  expect(mpinErrorText(apiError(409, { detail: 'MPIN not set' }, 'MPIN not set'), 'Could not confirm')).toBe('Set your MPIN first')
  expect(mpinErrorText(apiError(400, { detail: 'amount exceeds what is available (1,250.00)' },
    'amount exceeds what is available (1,250.00)'), 'Could not confirm')).toBe('amount exceeds what is available (1,250.00)')
  expect(mpinErrorText('boom', 'Could not confirm')).toBe('Could not confirm')

  const onConfirm = vi.fn().mockRejectedValue(apiError(400, { detail: 'minimum withdrawal is 50.00' }, 'minimum withdrawal is 50.00'))
  renderDialog(onConfirm)
  await typePin('123456')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('minimum withdrawal is 50.00')
})

test('only the MPIN-not-set 409 asks for an MPIN; any other 409 reads as the server says', async () => {
  expect(mpinErrorText(apiError(409, { detail: 'MPIN not set' }, 'MPIN not set'), 'x')).toBe('Set your MPIN first')
  // A transfer to the trading account before one is linked is also a 409.
  expect(mpinErrorText(apiError(409, { detail: 'no account linked yet' }, 'no account linked yet'), 'x'))
    .toBe('no account linked yet')
  expect(mpinErrorText(apiError(409, {}, 'refused'), 'x')).toBe('refused')

  const onConfirm = vi.fn().mockRejectedValue(apiError(409, { detail: 'no account linked yet' }, 'no account linked yet'))
  renderDialog(onConfirm)
  await typePin('123456')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('no account linked yet')
  expect(alert).not.toHaveTextContent('Set your MPIN first')
})

test('Cancel is the first focus and reports to the caller; closing forgets the PIN and the error', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { attempts_left: 4 }))
  const { onCancel, rerender } = renderDialog(onConfirm)
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  await screen.findByRole('alert')
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onCancel).toHaveBeenCalledTimes(1)

  rerender(
    <PinConfirmDialog open={false} title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}><p>x</p></PinConfirmDialog>,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
  rerender(
    <PinConfirmDialog open title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}><p>x</p></PinConfirmDialog>,
  )
  expect(screen.queryByRole('alert')).toBeNull()
  expect(boxes().map((b) => b.value).join('')).toBe('')
})
