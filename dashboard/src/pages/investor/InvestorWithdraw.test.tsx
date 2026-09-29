import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw, { feePreview } from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'
import { destinationFixture, summaryFixture, withdrawalFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary, PayoutDestination, PortalWithdrawal } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD',
  wallets: {
    main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 0, on_hold: 0, available: 0 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
  withdrawal_rules: { min: 50, fee_pct: 1.5 },
}
const salary: PayoutDestination = destinationFixture({
  id: 12, user_id: 1, kind: 'bank', nickname: 'Salary', summary: 'ICICI ••4543', status: 'approved',
  details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
  proof_file_id: null, decided_by: 2, decided_at: '2026-09-22T10:00:00Z', decision_note: null,
  created_at: '2026-09-21T10:00:00Z',
})
const pendingOne: PayoutDestination = destinationFixture({
  ...salary, id: 13, nickname: 'Pending one', summary: 'HDFC ••0001', status: 'pending',
  decided_by: null, decided_at: null,
})
const request: PortalWithdrawal = withdrawalFixture({
  id: 3, user_id: 1, destination_id: 12, destination_kind: 'bank', destination_summary: 'ICICI ••4543',
  amount: 1000, fee: 15, net_amount: 985, status: 'approved', decided_by: 2,
  decided_at: '2026-09-23T11:00:00Z', decision_note: null, paid_by: null, paid_at: null, txid: null,
  created_at: '2026-09-23T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: {
  refuse?: { status: number; body: unknown }; destinations?: PayoutDestination[]
  rows?: PortalWithdrawal[]; fail?: boolean; available?: number
} = {}) {
  const rows: PortalWithdrawal[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.available === undefined ? summary
        : { ...summary, wallets: { ...summary.wallets, main: { ...summary.wallets.main, available: opts.available } } })
    }
    if (url.endsWith('/investor/payout-destinations')) return jsonResponse(opts.destinations ?? [salary, pendingOne])
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse(opts.refuse.body, opts.refuse.status)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    if (/\/investor\/withdrawals\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      rows[0] = { ...rows[0], status: 'cancelled' }
      return jsonResponse(rows[0])
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

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
// Unmount before resetting the hide-balances store (see Money.test.tsx):
// otherwise the broadcast from setHidden(false) updates a still-mounted
// Money outside act().
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setHidden(false) })

test('feePreview rounds the fee half-up to cents and nets it off, exactly like the server', () => {
  expect(feePreview('1000', 1.5)).toEqual({ fee: 15, net: 985 })
  expect(feePreview('333.33', 1.5)).toEqual({ fee: 5, net: 328.33 })
  expect(feePreview('250', 0)).toEqual({ fee: 0, net: 250 })
  expect(feePreview('1,000', 1.5)).toBeNull()
  // Regression: floating-point `cents * feePct / 100` reads 1.15% of 250.00
  // as 28749.999999999996, one cent short of the server's Decimal result.
  expect(feePreview('250.00', 1.15)).toEqual({ fee: 2.88, net: 247.12 })
  // Exact half-cent boundaries, checked against the half-up rule: 5.00 at
  // 0.7% is precisely 0.035 (rounds up to 0.04); at 2.3% precisely 0.115
  // (rounds up to 0.12).
  expect(feePreview('5.00', 0.7)).toEqual({ fee: 0.04, net: 4.96 })
  expect(feePreview('5.00', 2.3)).toEqual({ fee: 0.12, net: 4.88 })
})

test('feePreview gives up on an amount too large to be a number', () => {
  // Number('9' x 400) is Infinity, and BigInt(Infinity) throws a RangeError --
  // which, during render, took the whole page down.
  expect(feePreview('9'.repeat(400), 1.5)).toBeNull()
  expect(feePreview('9'.repeat(400) + '.99', 1.5)).toBeNull()
})

test('pasting an absurdly long amount does not crash the page', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,020.50 USD')
  const input = screen.getByLabelText('Amount in USD')
  await userEvent.click(input)
  await userEvent.paste('9'.repeat(400))
  expect(input).toHaveValue('9'.repeat(400))
  expect(screen.getByRole('heading', { level: 1, name: 'Withdraw' })).toBeInTheDocument()
  expect(screen.queryByText(/You receive/)).not.toBeInTheDocument()
})

test('shows what is available, previews the fee, and files a request only after the MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('5,020.50 USD')).toBeInTheDocument()
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Withdraw' })).toBeInTheDocument()
  expect(document.title).toBe('Withdraw · MirrorFleet')
  // Only approved payout accounts are offered.
  expect(screen.getByRole('option', { name: 'Salary · ICICI ••4543' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: /Pending one/ })).not.toBeInTheDocument()

  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  expect(screen.getByText(/Fee 15\.00 USD \(1\.5%\) · You receive 985\.00 USD/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))

  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to ICICI ••4543?' })
  expect(within(dialog).getByText('985.00 USD')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Send request' })).toBeDisabled()
  expect(posts(fetchMock)).toHaveLength(0)

  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/withdrawals$/)
  expect(JSON.parse((post[1] as RequestInit).body as string))
    .toEqual({ destination_id: 12, amount: '1000', mpin: '123456' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText('Approved, payment pending')).toBeInTheDocument()
  expect(screen.getByText('1,000.00 USD')).toBeInTheDocument()
})

test('Cancel on the review sends nothing and keeps the form', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to ICICI ••4543?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('1000')
})

test('Use max fills the floored available amount, and that is what is posted', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USD')
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('5020.50')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 5,020.50 USD to ICICI ••4543?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string))
    .toEqual({ destination_id: 12, amount: '5020.50', mpin: '123456' })
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '0')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('a wrong MPIN stays in the dialog with the tries left, and never bounces to /login', async () => {
  const fetchMock = mockRoutes({ refuse: { status: 401, body: { detail: 'Invalid MPIN', attempts_left: 2 } } })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '111111')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await screen.findByText('Wrong MPIN, 2 tries left')).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(1)
  expect(screen.queryByText('Unauthorized')).not.toBeInTheDocument()
})

test("the server's money refusal is shown as written, inside the dialog", async () => {
  mockRoutes({ refuse: { status: 400, body: { detail: 'amount exceeds what is available (1,250.00)' } } })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '9999')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await within(dialog).findByText(/exceeds what is available \(1,250\.00\)/)).toBeInTheDocument()
})

test('the timeline names the current step in words and with aria-current', async () => {
  mockRoutes({ rows: [request] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const progress = await screen.findByRole('list', { name: 'Withdrawal progress' })
  const steps = within(progress).getAllByRole('listitem')
  expect(steps).toHaveLength(3)
  expect(steps[0]).not.toHaveAttribute('aria-current')
  expect(steps[1]).toHaveAttribute('aria-current', 'step')
  expect(steps[1]).toHaveTextContent('Approved, payment pending')
  expect(steps[1]).toHaveTextContent('current')
  expect(steps[2]).not.toHaveAttribute('aria-current')
  expect(steps[2]).not.toHaveTextContent('current')
  // Fee and Net each render through <Money>, so they are separate nodes;
  // toHaveTextContent reads the row's full (deep) text, unlike getByText
  // which only ever looks at a single node's own text.
  const row = screen.getByText('1,000.00 USD').closest('li')!
  expect(row).toHaveTextContent(/Fee 15\.00 USD · Net 985\.00 USD/)
})

test('hiding balances masks the history row\'s fee and net too, not just the amount', async () => {
  mockRoutes({ rows: [request] })
  setHidden(true)
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByRole('list', { name: 'Withdrawal progress' })
  expect(screen.queryByText(/15\.00/)).not.toBeInTheDocument()
  expect(screen.queryByText(/985\.00/)).not.toBeInTheDocument()
  expect(screen.queryByText(/1,000\.00/)).not.toBeInTheDocument()
})

test('with no approved payout account the form is replaced by a link to add one', async () => {
  mockRoutes({ destinations: [pendingOne] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'Add a payout account' })
  expect(link).toHaveAttribute('href', '/org/1/invest/payout-accounts')
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})

const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'

test.each(['1000.005', '1,000'])('%s is refused before the review, and nothing is posted', async (amount) => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), amount)
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText(TWO_DECIMALS)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('Use max is disabled when nothing is available', async () => {
  mockRoutes({ available: 0 })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USD')
  expect(screen.getByRole('button', { name: 'Use max' })).toBeDisabled()
})

test('a requested withdrawal can be cancelled after a confirmation', async () => {
  const fetchMock = mockRoutes({ rows: [{ ...request, status: 'requested', decided_by: null, decided_at: null }] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Cancel withdrawal 3' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel withdrawal #3?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/withdrawals\/3\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel withdrawal 3' })).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No requests yet')).toBeInTheDocument()
})
