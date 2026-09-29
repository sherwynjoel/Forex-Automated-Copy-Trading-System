import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT, formatBytes } from './FileInput'

// jsdom has no object URLs; the thumbnail needs one.
const urlApi = URL as unknown as {
  createObjectURL?: (f: Blob) => string
  revokeObjectURL?: (u: string) => void
}

beforeEach(() => {
  urlApi.createObjectURL = vi.fn(() => 'blob:preview')
  urlApi.revokeObjectURL = vi.fn()
})

afterEach(() => {
  delete urlApi.createObjectURL
  delete urlApi.revokeObjectURL
  vi.restoreAllMocks()
})

function Harness({ onChange, required, error, hint }: {
  onChange?: (f: File | null) => void; required?: boolean; error?: string | null; hint?: string
}) {
  const [value, setValue] = useState<File | null>(null)
  return (
    <FileInput
      id="receipt" label="Receipt" accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES}
      value={value} onChange={(f) => { setValue(f); onChange?.(f) }}
      required={required} error={error} hint={hint}
    />
  )
}

const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'receipt.png', { type: 'image/png' })

test('the constants match the server limits', () => {
  expect(RECEIPT_ACCEPT).toEqual(['image/jpeg', 'image/png', 'image/webp', 'application/pdf'])
  expect(MAX_UPLOAD_BYTES).toBe(5 * 1024 * 1024)
  expect(formatBytes(512)).toBe('512 B')
  expect(formatBytes(48 * 1024)).toBe('48 KB')
  expect(formatBytes(5 * 1024 * 1024)).toBe('5 MB')
  expect(formatBytes(1.5 * 1024 * 1024)).toBe('1.5 MB')
})

test('the input is labelled, accepts the receipt types and Choose file opens the native picker', async () => {
  render(<Harness />)
  const input = screen.getByLabelText('Receipt') as HTMLInputElement
  expect(input.type).toBe('file')
  expect(input).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp,application/pdf')
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  // click() lives on HTMLElement.prototype in jsdom; the button forwards it
  // to the hidden input, so the input must be among the spy's receivers.
  const open = vi.spyOn(HTMLElement.prototype, 'click')
  await userEvent.click(screen.getByRole('button', { name: 'Choose file' }))
  expect(open.mock.instances).toContain(input)
})

test('an accepted image shows its name, size and a thumbnail; Remove clears it and revokes the URL', async () => {
  const onChange = vi.fn()
  const { container } = render(<Harness onChange={onChange} />)
  const file = png()
  await userEvent.upload(screen.getByLabelText('Receipt'), file)
  expect(onChange).toHaveBeenLastCalledWith(file)
  expect(screen.getByText('receipt.png')).toBeInTheDocument()
  expect(screen.getByText('(4 B)')).toBeInTheDocument()
  expect(urlApi.createObjectURL).toHaveBeenCalledWith(file)
  expect(container.querySelector('img')).toHaveAttribute('src', 'blob:preview')
  expect(screen.queryByRole('alert')).toBeNull()

  await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
  expect(onChange).toHaveBeenLastCalledWith(null)
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  expect(container.querySelector('img')).toBeNull()
  expect(urlApi.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
})

test('a type outside the accept list is refused before anything is chosen', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  // user-event discards non-matching files itself unless told not to; the
  // component's own check is what is under test here.
  const user = userEvent.setup({ applyAccept: false })
  await user.upload(screen.getByLabelText('Receipt'), new File(['hi'], 'notes.txt', { type: 'text/plain' }))
  expect(screen.getByRole('alert')).toHaveTextContent('That file type is not accepted')
  expect(onChange).toHaveBeenLastCalledWith(null)
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  expect(screen.getByLabelText('Receipt')).toHaveAttribute('aria-invalid', 'true')
})

test('a file over the limit is refused with the limit named', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], 'big.png', { type: 'image/png' })
  await userEvent.upload(screen.getByLabelText('Receipt'), big)
  expect(screen.getByRole('alert')).toHaveTextContent('File is larger than 5 MB')
  expect(onChange).toHaveBeenLastCalledWith(null)
})

test('required marks the input and shows in the hint; a caller error is announced', () => {
  render(<Harness required hint="JPEG, PNG, WebP or PDF up to 5 MB." error="Upload the receipt first" />)
  const input = screen.getByLabelText('Receipt')
  expect(input).toBeRequired()
  expect(screen.getByText('Required. JPEG, PNG, WebP or PDF up to 5 MB.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Upload the receipt first')
  expect(input).toHaveAccessibleDescription(/Upload the receipt first/)
})
