import { readFileSync } from 'node:fs'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'
import { accountSummaryFixture, depositFixture, methodFixture, summaryFixture } from '../../test/portalFixtures'
import type { AccountSummary, InvestorSummary, PaymentMethod, PortalDeposit } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
// The page loads the encoder with `await import('qrcode')` and takes the
// named export; keep `default` too so either shape resolves.
vi.mock('qrcode', () => {
  const toDataURL = vi.fn(async () => 'data:image/png;base64,QR')
  return { toDataURL, default: { toDataURL } }
})
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', deposits_open: true,
  accounts: [accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null })],
}
const crypto: PaymentMethod = methodFixture({
  id: 5, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
  details: { coin: 'USDT', network: 'TRC20', address: 'TAddr123' },
  min_amount: 0, fee_pct: 0, instructions: null, sort_order: 0,
})
const bank: PaymentMethod = methodFixture({
  id: 6, kind: 'bank', label: 'ICICI Bank', enabled: true, currency: 'USD',
  details: { bank_name: 'ICICI Bank', holder: 'MirrorFleet Ltd', account_number: '000112344543', code: 'ICIC0001' },
  min_amount: 500, fee_pct: 1.5, instructions: 'Quote the reference in the transfer remarks.', sort_order: 1,
})
const notice: PortalDeposit = depositFixture({
  id: 1, user_id: 1, method_id: 5, method_kind: 'crypto', method_label: 'USDT on TRC20',
  amount: 250, fee: 0, credited_amount: null, reference: 'abc', receipt_file_id: null,
  target: 'wallet', target_account_id: null, note: null, status: 'pending',
  decided_by: null, decided_at: null, decision_note: null, created_at: '2026-09-23T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: {
  open?: boolean; linked?: boolean; rows?: PortalDeposit[]; fail?: boolean; methods?: PaymentMethod[]
  accounts?: AccountSummary[]
} = {}) {
  const deposits: PortalDeposit[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse({
        ...summary, deposits_open: opts.open ?? true,
        ...(opts.linked === false ? { accounts: [] } : {}),
        ...(opts.accounts ? { accounts: opts.accounts } : {}),
      })
    }
    if (url.endsWith('/investor/payment-methods')) {
      return jsonResponse(opts.open === false ? [] : (opts.methods ?? [crypto, bank]))
    }
    if (url.endsWith('/investor/files') && init?.method === 'POST') {
      return jsonResponse({ id: 77, purpose: 'deposit_receipt', content_type: 'image/png', size_bytes: 3,
                            created_at: '2026-09-23T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') {
      deposits.unshift(notice)
      return jsonResponse(notice, 201)
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(deposits)
    if (/\/investor\/deposits\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      deposits[0] = { ...deposits[0], status: 'cancelled' }
      return jsonResponse(deposits[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  // FileInput previews images through an object URL; jsdom has none.
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('shows the crypto method with a QR and files a notice with the exact payload', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getAllByText(/USDT on TRC20/).length).toBeGreaterThan(0)
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')
  expect(screen.getByRole('heading', { level: 1, name: 'Deposit' })).toBeInTheDocument()
  expect(document.title).toBe('Deposit · MirrorFleet')
  expect(screen.getByRole('tab', { name: 'Crypto' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Bank' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: '250' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('250')
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))

  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'wallet', target_account_id: null, note: null,
  })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
  expect(screen.getByText('250.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel deposit 1' })).toBeInTheDocument()
})

test('the Bank tab lists bank details with a Copy button each, needs a receipt, and uploads it before filing', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('tab', { name: 'Bank' }))
  expect(await screen.findByText('000112344543')).toBeInTheDocument()
  expect(screen.getByText('Quote the reference in the transfer remarks.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy Account number' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy SWIFT / IFSC code' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Min' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('500.00')
  await userEvent.type(screen.getByLabelText('Bank transaction ID'), 'UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  expect(await screen.findByText('A receipt is required for bank deposits')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)

  const file = new File(['png'], 'receipt.png', { type: 'image/png' })
  await userEvent.upload(screen.getByLabelText(/^Receipt/), file)
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(2))
  const [upload, filed] = posts(fetchMock)
  expect(String(upload[0])).toMatch(/\/investor\/files$/)
  const form = (upload[1] as RequestInit).body as FormData
  expect(form.get('purpose')).toBe('deposit_receipt')
  expect((form.get('file') as File).name).toBe('receipt.png')
  expect(String(filed[0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((filed[1] as RequestInit).body as string)).toEqual({
    method_id: 6, amount: '500.00', reference: 'UTR123', receipt_file_id: 77,
    target: 'wallet', target_account_id: null, note: null,
  })
})

test('switching kind or method clears a stale bank-receipt error', async () => {
  const bank2: PaymentMethod = methodFixture({
    id: 7, kind: 'bank', label: 'HDFC Bank', enabled: true, currency: 'USD',
    details: { bank_name: 'HDFC Bank', holder: 'MirrorFleet Ltd', account_number: '000998877665', code: 'HDFC0001' },
    min_amount: 100, fee_pct: 1, instructions: null, sort_order: 2,
  })
  mockRoutes({ methods: [crypto, bank, bank2] })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('tab', { name: 'Bank' }))
  await screen.findByText('000112344543')
  await userEvent.type(screen.getByLabelText('Amount in USD'), '500')
  await userEvent.type(screen.getByLabelText('Bank transaction ID'), 'UTR1')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  expect(await screen.findByText('A receipt is required for bank deposits')).toBeInTheDocument()

  // Picking a different method of the same kind clears it too.
  await userEvent.click(screen.getByRole('button', { name: /HDFC Bank/ }))
  expect(screen.queryByText('A receipt is required for bank deposits')).not.toBeInTheDocument()

  // Trigger it again, then switch kind: the label goes back to optional and
  // the stale "required" text does not survive onto it.
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  expect(await screen.findByText('A receipt is required for bank deposits')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Crypto' }))
  expect(screen.queryByText('A receipt is required for bank deposits')).not.toBeInTheDocument()
  expect(screen.getByText('Receipt (optional)')).toBeInTheDocument()
})

test('Trading account is offered as the target when an account is linked, and is posted with its id', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('radio', { name: 'Trading account' }))
  expect(screen.queryByLabelText('Which trading account')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '250' }))
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'account', target_account_id: 1001, note: null,
  })
})

test('with several accounts the notice names the one picked', async () => {
  const fetchMock = mockRoutes({ accounts: [
    accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null }),
    accountSummaryFixture({ account_id: 1002, nickname: 'Swing', mt5_login: 6002 }),
  ] })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  expect(screen.queryByLabelText('Which trading account')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('radio', { name: 'Trading account' }))
  const which = screen.getByLabelText('Which trading account')
  expect(within(which).getAllByRole('option').map((o) => o.textContent)).toEqual(['Inv', 'MT5 6002'])
  await userEvent.selectOptions(which, '1002')
  await userEvent.click(screen.getByRole('button', { name: '250' }))
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'account', target_account_id: 1002, note: null,
  })
})

test('a submit never names an account a refresh no longer owns', async () => {
  // Account 1002 is picked while it is still linked; a refresh afterwards
  // (here, the one `cancel` triggers) finds the investor down to just
  // 1001 -- an admin unlinked 1002 meanwhile. The stale pick must not reach
  // the server: the next submit falls back to the account that is still there.
  let summaryCalls = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      summaryCalls += 1
      const accounts = summaryCalls === 1
        ? [accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null }),
           accountSummaryFixture({ account_id: 1002, nickname: 'Swing', mt5_login: 6002 })]
        : [accountSummaryFixture({ account_id: 1001, nickname: 'Inv', mt5_login: null })]
      return jsonResponse({ ...summary, accounts })
    }
    if (url.endsWith('/investor/payment-methods')) return jsonResponse([crypto, bank])
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') return jsonResponse(notice, 201)
    if (url.endsWith('/investor/deposits')) return jsonResponse([{ ...notice, status: 'pending' }])
    if (/\/investor\/deposits\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      return jsonResponse({ ...notice, status: 'cancelled' })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('radio', { name: 'Trading account' }))
  await userEvent.selectOptions(screen.getByLabelText('Which trading account'), '1002')

  await userEvent.click(screen.getByRole('button', { name: 'Cancel deposit 1' }))
  await userEvent.click(screen.getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(screen.getByText('Notice cancelled.')).toBeInTheDocument())
  expect(screen.queryByLabelText('Which trading account')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: '250' }))
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'xyz')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock).some(([u]) => String(u).endsWith('/investor/deposits'))).toBe(true))
  const post = posts(fetchMock).find(([u]) => String(u).endsWith('/investor/deposits'))!
  expect(JSON.parse((post[1] as RequestInit).body as string)).toMatchObject({
    target: 'account', target_account_id: 1001,
  })
})

test('without a linked account only My wallet is offered', async () => {
  mockRoutes({ linked: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  expect(screen.getByRole('radio', { name: 'My wallet' })).toBeChecked()
  expect(screen.queryByRole('radio', { name: 'Trading account' })).not.toBeInTheDocument()
})

test('Copy address copies, says so, and reverts after two seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  // Installed AFTER render: user-event swaps in its own clipboard stub on
  // first use, and this one must be the one the page hits.
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('TAddr123'))
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  expect(screen.getByText('Address copied to the clipboard.')).toBeInTheDocument()

  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(await screen.findByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('without a clipboard API, Copy address selects the address instead', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  expect(await screen.findByText(/address selected/i)).toBeInTheDocument()
  expect(window.getSelection()?.toString()).toBe('TAddr123')
  expect(screen.getByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('says deposits are not open when no method is enabled, and what happens next', async () => {
  mockRoutes({ open: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.getByText(/Your admin has not added a payment method yet/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'File deposit notice' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the closed state shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ open: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the QR encoder is loaded on demand, not in the main bundle', () => {
  const source = readFileSync('src/pages/investor/InvestorDeposit.tsx', 'utf8')
  expect(source).not.toMatch(/^import .* from 'qrcode'/m)
  expect(source).toContain("await import('qrcode')")
})

test('a pending notice can be cancelled after a confirmation', async () => {
  const fetchMock = mockRoutes({ rows: [notice] })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Cancel deposit 1' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel deposit notice #1?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/deposits\/1\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel deposit 1' })).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No notices yet')).toBeInTheDocument()
})
