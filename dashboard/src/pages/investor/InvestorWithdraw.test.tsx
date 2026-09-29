import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const LONG = 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f'
const wallet = { coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: null }
const summary = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true, total_deposited: 5000, total_withdrawn: 0,
  net_deposits: 5000, pending_withdrawn: 0, equity: 5120.5, profit: 120.5, available: 5120.5,
}
const request = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                  status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                  decided_by: 2, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { refuse?: string; unlinked?: boolean; noWallet?: boolean;
                            rows?: unknown[] } = {}) {
  const rows: unknown[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.unlinked
        ? { ...summary, link_state: 'unlinked', account: null, available: null } : summary)
    }
    if (url.endsWith('/investor/wallet')) {
      return opts.noWallet
        ? jsonResponse({ detail: 'Deposits are not open yet' }, 404)
        : jsonResponse(wallet)
    }
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse({ detail: opts.refuse }, 400)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); facts.supportEmail = '' })

test('shows what is available with its unit and files a request only after the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('5,120.50 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Withdraw' })).toBeInTheDocument()
  expect(document.title).toBe('Withdraw · MirrorFleet')

  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), LONG)
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))

  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USDT to T…9f?' })
  expect(within(dialog).getByText(LONG)).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)

  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/withdrawals$/)
  expect(JSON.parse((post[1] as RequestInit).body as string))
    .toEqual({ amount: '1000', destination: LONG })
  await waitFor(() => expect(screen.getByText('Approved, payment pending')).toBeInTheDocument())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByText('1,000.00 USDT')).toBeInTheDocument()
})

test('Cancel on the review sends nothing and keeps the form', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USDT to TDest?' })

  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.getByLabelText('Amount in USDT')).toHaveValue('1000')
  expect(screen.getByLabelText('Destination address')).toHaveValue('TDest')
})

test('Use max fills the available amount, and that is what is posted', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USDT')
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USDT')).toHaveValue('5120.50')

  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 5,120.50 USDT to TDest?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string))
    .toEqual({ amount: '5120.50', destination: 'TDest' })
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '0')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('without a wallet the figures fall back to the account currency', async () => {
  mockRoutes({ noWallet: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,120.50 USD')
  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to TDest?' })).toBeInTheDocument()
})

test("the server's refusal is shown as written", async () => {
  mockRoutes({ refuse: 'amount exceeds what is available to withdraw (1120.50)' })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,120.50 USD')
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '9999')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await screen.findByText(/available to withdraw \(1120\.50\)/)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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
})

test('unlinked investors cannot request yet, and are told what happens next', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ unlinked: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText('Withdrawals open once your admin links your trading account.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'help@desk.example' })).toHaveAttribute('href', 'mailto:help@desk.example')
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})
