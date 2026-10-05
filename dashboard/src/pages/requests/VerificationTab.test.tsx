import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import VerificationTab from './VerificationTab'
import { profileFixture } from '../../test/portalFixtures'
import type { KycProfile } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const submitted: KycProfile = profileFixture({
  user_id: 5, status: 'submitted', submitted_at: '2026-10-01T09:00:00Z', email: 'ada@example.com', display_name: 'Ada',
})
const approved: KycProfile = profileFixture({
  user_id: 6, status: 'approved', email: 'bob@example.com', display_name: 'Bob', full_name: 'Bob Builder',
})

function mockRoutes() {
  let rows = [{ ...submitted }, { ...approved }]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/kyc')) return jsonResponse(rows)
    const m = url.match(/\/kyc\/(\d+)\/decision$/)
    if (m && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { status: KycProfile['status'] }
      rows = rows.map((r) => r.user_id === Number(m[1]) ? { ...r, status: body.status } : r)
      return jsonResponse(rows.find((r) => r.user_id === Number(m[1])))
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the open view lists submitted profiles; All adds the decided ones', async () => {
  mockRoutes()
  const { rerender } = render(<VerificationTab orgId={1} control show="open" onDone={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.getByText('Under review')).toBeInTheDocument()
  expect(screen.queryByText('Bob')).not.toBeInTheDocument()
  rerender(<VerificationTab orgId={1} control show="all" onDone={vi.fn()} />)
  expect(screen.getByText('Bob')).toBeInTheDocument()
  expect(screen.getByText('Verified')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve verification 6' })).not.toBeInTheDocument()
})

test('details show every field and the four documents', async () => {
  mockRoutes()
  render(<VerificationTab orgId={1} control show="open" onDone={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Details of verification 5' }))
  const drawer = await screen.findByRole('dialog', { name: 'Verification of Ada' })
  expect(within(drawer).getByText('P1234567')).toBeInTheDocument()
  expect(within(drawer).getByText('Passport')).toBeInTheDocument()
  for (const label of ['ID front', 'ID back', 'Proof of address', 'Your photo']) {
    expect(within(drawer).getByRole('img', { name: label })).toHaveAttribute('src', expect.stringMatching(/^\/api\/orgs\/1\/files\/3[1-4]$/))
  }
})

test('a rejection needs a note; an approval posts and reports back', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<VerificationTab orgId={1} control show="open" onDone={onDone} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Reject verification 5' }))
  let dialog = await screen.findByRole('dialog', { name: 'Reject the verification of Ada' })
  expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled()
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve verification 5' }))
  dialog = await screen.findByRole('dialog', { name: 'Approve the verification of Ada' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Verification approved'))
  const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/kyc/5/decision'))!
  expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ status: 'approved', note: '' })
  expect(await screen.findByText('No open verifications')).toBeInTheDocument()
})

test('a viewer sees the queue but no decisions', async () => {
  mockRoutes()
  render(<VerificationTab orgId={1} control={false} show="open" onDone={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve verification 5' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Details of verification 5' })).toBeInTheDocument()
})

test('a failed load stays in the tab with Retry, never an empty queue', async () => {
  let fail = true
  vi.stubGlobal('fetch', vi.fn(async () => fail
    ? jsonResponse({ detail: 'boom' }, 500)
    : jsonResponse([{ ...submitted }])))
  render(<VerificationTab orgId={1} control show="open" onDone={vi.fn()} />)
  expect(await screen.findByText('boom')).toBeInTheDocument()
  expect(screen.queryByText('No open verifications')).not.toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText('boom')).not.toBeInTheDocument()
})
