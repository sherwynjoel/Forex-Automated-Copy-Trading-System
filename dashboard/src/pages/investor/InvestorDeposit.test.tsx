import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,QR') } }))

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
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the wallet card with a QR and files a notice', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getByText(/USDT on TRC20/)).toBeInTheDocument()
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')

  await userEvent.type(screen.getByLabelText('Amount'), '5000')
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'I have sent it' }))

  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual(
    { amount: '5000', coin: 'USDT', txid: 'abc', note: '' })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
})

test('says deposits are not open when there is no wallet', async () => {
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'I have sent it' })).not.toBeInTheDocument()
})
