import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorPayoutAccounts from './InvestorPayoutAccounts'
import { mockUseOrg } from '../../test/orgMock'
import { destinationFixture } from '../../test/portalFixtures'
import type { PayoutDestination } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const salary: PayoutDestination = destinationFixture({
  id: 12, user_id: 1, kind: 'bank', nickname: 'Salary', summary: 'ICICI ••4543', status: 'approved',
  details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
  proof_file_id: null, decided_by: 2, decided_at: '2026-09-22T10:00:00Z', decision_note: null,
  created_at: '2026-09-21T10:00:00Z',
})
const tron: PayoutDestination = destinationFixture({
  ...salary, id: 14, kind: 'crypto', nickname: 'Tron', summary: 'TRC20 T…9f', status: 'pending',
  details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
  decided_by: null, decided_at: null,
})

function mockRoutes(opts: {
  rows?: PayoutDestination[]; fail?: boolean; removeRefused?: string
  /** The FIRST destination POST answers a wrong MPIN (401); every later one succeeds. */
  wrongMpinOnce?: boolean
} = {}) {
  const rows: PayoutDestination[] = [...(opts.rows ?? [salary, tron])]
  let destinationPosts = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/files') && init?.method === 'POST') {
      return jsonResponse({ id: 78, purpose: 'payout_proof', content_type: 'image/png', size_bytes: 3,
                            created_at: '2026-09-23T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/payout-destinations') && init?.method === 'POST') {
      destinationPosts += 1
      if (opts.wrongMpinOnce && destinationPosts === 1) {
        return jsonResponse({ detail: 'Invalid MPIN', attempts_left: 2 }, 401)
      }
      const body = JSON.parse(init.body as string) as { kind: 'bank' | 'crypto'; nickname: string; details: Record<string, string> }
      const created: PayoutDestination = destinationFixture({
        ...salary, id: 20, kind: body.kind, nickname: body.nickname, details: body.details, status: 'pending',
        summary: body.kind === 'bank' ? 'ICICI ••4543' : 'TRC20 T…9f', decided_by: null, decided_at: null,
      })
      rows.unshift(created)
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/payout-destinations')) return jsonResponse(rows)
    if (/\/investor\/payout-destinations\/\d+\/remove$/.test(url) && init?.method === 'POST') {
      if (opts.removeRefused) return jsonResponse({ detail: opts.removeRefused }, 409)
      const id = Number(url.match(/\/(\d+)\/remove$/)![1])
      const idx = rows.findIndex((r) => r.id === id)
      const removed = { ...rows[idx], status: 'removed' as const }
      rows.splice(idx, 1)
      return jsonResponse(removed)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists bank and crypto payout accounts with their status, heading and title', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  expect(await screen.findByText('Salary')).toBeInTheDocument()
  expect(screen.getByText('ICICI ••4543')).toBeInTheDocument()
  expect(screen.getByText('Approved')).toBeInTheDocument()
  expect(screen.getByText('Tron')).toBeInTheDocument()
  expect(screen.getByText('Pending review')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Remove Salary' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Payout accounts' })).toBeInTheDocument()
  expect(document.title).toBe('Payout accounts · MirrorFleet')
})

test('adding a bank account asks for the MPIN and posts the exact payload', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  expect(await screen.findByText('No bank accounts yet')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add bank account' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add bank account' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Salary')
  await userEvent.type(within(drawer).getByLabelText('Bank name'), 'ICICI Bank')
  await userEvent.type(within(drawer).getByLabelText('Account holder'), 'S Joel')
  await userEvent.type(within(drawer).getByLabelText('Account number'), '000112344543')
  await userEvent.type(within(drawer).getByLabelText('SWIFT / IFSC code'), 'ICIC0001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))

  const confirm = await screen.findByRole('dialog', { name: 'Save Salary as a payout account?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await enterPin(confirm, '123456')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/payout-destinations$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    kind: 'bank', nickname: 'Salary',
    details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
    proof_file_id: null, mpin: '123456',
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(await screen.findByText('Salary')).toBeInTheDocument()
  expect(screen.getByText(/An admin approves it before it can be used/)).toBeInTheDocument()
})

test('adding a crypto address uploads the proof first and posts its file id', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await screen.findByText('No crypto addresses yet')
  await userEvent.click(screen.getByRole('button', { name: 'Add crypto address' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add crypto address' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Tron')
  await userEvent.type(within(drawer).getByLabelText('Coin'), 'USDT')
  await userEvent.type(within(drawer).getByLabelText('Network'), 'TRC20')
  await userEvent.type(within(drawer).getByLabelText('Address'), 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')
  await userEvent.upload(within(drawer).getByLabelText(/^Proof/), new File(['png'], 'wallet.png', { type: 'image/png' }))
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))

  const confirm = await screen.findByRole('dialog', { name: 'Save Tron as a payout account?' })
  // The proof went up before the MPIN was asked for, so a wrong PIN never re-uploads it.
  expect(posts(fetchMock)).toHaveLength(1)
  expect((posts(fetchMock)[0][1] as RequestInit).body).toBeInstanceOf(FormData)
  expect(((posts(fetchMock)[0][1] as RequestInit).body as FormData).get('purpose')).toBe('payout_proof')
  await enterPin(confirm, '123456')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(2))
  expect(JSON.parse((posts(fetchMock)[1][1] as RequestInit).body as string)).toEqual({
    kind: 'crypto', nickname: 'Tron',
    details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
    proof_file_id: 78, mpin: '123456',
  })
})

test('a wrong MPIN stays in the dialog without navigating, and the retry reuses the already-uploaded proof', async () => {
  const fetchMock = mockRoutes({ rows: [], wrongMpinOnce: true })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await screen.findByText('No crypto addresses yet')
  await userEvent.click(screen.getByRole('button', { name: 'Add crypto address' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add crypto address' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Tron')
  await userEvent.type(within(drawer).getByLabelText('Coin'), 'USDT')
  await userEvent.type(within(drawer).getByLabelText('Network'), 'TRC20')
  await userEvent.type(within(drawer).getByLabelText('Address'), 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')
  await userEvent.upload(within(drawer).getByLabelText(/^Proof/), new File(['png'], 'wallet.png', { type: 'image/png' }))
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))

  const confirm = await screen.findByRole('dialog', { name: 'Save Tron as a payout account?' })
  await enterPin(confirm, '111111')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  expect(await within(confirm).findByText('Wrong MPIN, 2 tries left')).toBeInTheDocument()
  // Still the same dialog, not a bounce to /login: this fails if
  // `{ redirectOn401: false }` were ever dropped from the destination POST.
  expect(screen.getByRole('dialog', { name: 'Save Tron as a payout account?' })).toBeInTheDocument()
  expect(screen.queryByText('Unauthorized')).not.toBeInTheDocument()

  await enterPin(confirm, '123456')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

  const filePosts = posts(fetchMock).filter(([url]) => String(url).endsWith('/investor/files'))
  const destinationPosts = posts(fetchMock).filter(([url]) => String(url).endsWith('/investor/payout-destinations'))
  // One upload across both attempts: this fails if the upload were moved
  // into confirm() and so re-ran on the retry.
  expect(filePosts).toHaveLength(1)
  expect(destinationPosts).toHaveLength(2)
  const firstBody = JSON.parse((destinationPosts[0][1] as RequestInit).body as string)
  const secondBody = JSON.parse((destinationPosts[1][1] as RequestInit).body as string)
  expect(firstBody.proof_file_id).toBe(78)
  expect(secondBody).toEqual({
    kind: 'crypto', nickname: 'Tron',
    details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
    proof_file_id: 78, mpin: '123456',
  })
})

test('a required field that is blank stops the save before any MPIN is asked for', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await screen.findByText('No bank accounts yet')
  await userEvent.click(screen.getByRole('button', { name: 'Add bank account' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add bank account' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Salary')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))
  expect(await within(drawer).findByText('Bank name is required')).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: /as a payout account\?/ })).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('Remove asks first, posts the remove route, and drops the row', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Remove Salary' }))
  const dialog = await screen.findByRole('dialog', { name: 'Remove Salary?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/payout-destinations\/12\/remove$/)
  await waitFor(() => expect(screen.queryByText('Salary')).not.toBeInTheDocument())
  expect(screen.getByText('Tron')).toBeInTheDocument()
})

test("the server's refusal to remove is shown as written", async () => {
  mockRoutes({ removeRefused: 'a withdrawal is still using this payout account' })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Remove Salary' }))
  const dialog = await screen.findByRole('dialog', { name: 'Remove Salary?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
  expect(await screen.findByText('a withdrawal is still using this payout account')).toBeInTheDocument()
  expect(screen.getByText('Salary')).toBeInTheDocument()
})

test('dismissing a load error shows the empty states, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No bank accounts yet')).toBeInTheDocument()
  expect(screen.getByText('No crypto addresses yet')).toBeInTheDocument()
})
