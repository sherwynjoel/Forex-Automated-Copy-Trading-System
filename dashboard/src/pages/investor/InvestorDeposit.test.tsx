import { readFileSync } from 'node:fs'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'

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

const wallet = { coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: null }
const notice = { id: 1, user_id: 1, account_id: null, amount: 5000, coin: 'USDT', txid: 'abc',
                 note: null, status: 'pending', decided_by: null, decided_at: null,
                 decision_note: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { wallet?: boolean } = {}) {
  const deposits: unknown[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/wallet')) {
      return opts.wallet === false
        ? jsonResponse({ detail: 'Deposits are not open yet' }, 404)
        : jsonResponse(wallet)
    }
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') {
      deposits.unshift(notice)
      return jsonResponse(notice, 201)
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(deposits)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('shows the wallet card with a QR and files a notice', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getByText(/USDT on TRC20/)).toBeInTheDocument()
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')
  expect(screen.getByRole('heading', { level: 1, name: 'Deposit' })).toBeInTheDocument()
  expect(document.title).toBe('Deposit · MirrorFleet')

  await userEvent.type(screen.getByLabelText('Amount in USDT'), '5000')
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'I have sent it' }))

  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(String(post![0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual(
    { amount: '5000', coin: 'USDT', txid: 'abc', note: '' })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
  // The row's amount carries its coin.
  expect(screen.getByText('5,000.00 USDT')).toBeInTheDocument()
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

  await vi.advanceTimersByTimeAsync(2000)
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

test('says deposits are not open when there is no wallet, and what happens next', async () => {
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.getByText(
    "Your admin links your trading account; deposits open once the workspace's wallet is set.",
  )).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'I have sent it' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the closed state shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the QR encoder is loaded on demand, not in the main bundle', () => {
  const source = readFileSync('src/pages/investor/InvestorDeposit.tsx', 'utf8')
  expect(source).not.toMatch(/^import .* from 'qrcode'/m)
  expect(source).toContain("await import('qrcode')")
})
