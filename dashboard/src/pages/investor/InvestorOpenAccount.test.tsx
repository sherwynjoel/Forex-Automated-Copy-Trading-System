import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorOpenAccount from './InvestorOpenAccount'
import { mockUseOrg } from '../../test/orgMock'
import { accountRequestFixture, packageFixture, summaryFixture } from '../../test/portalFixtures'
import type { AccountRequest, KycStatus } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockRoutes(opts: { kyc?: KycStatus; requests?: AccountRequest[]; limit?: { max: number; used: number } } = {}) {
  let requests = [...(opts.requests ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(summaryFixture({ kyc_status: opts.kyc ?? 'approved',
                                           account_limit: opts.limit ?? { max: 5, used: 0 } }))
    }
    if (url.endsWith('/investor/account-packages')) {
      return jsonResponse([packageFixture(), packageFixture({ id: 2, name: 'Pro', min_deposit: 1000, leverage_options: [100] })])
    }
    if (url.endsWith('/investor/account-requests') && method === 'POST') {
      const body = JSON.parse(String(init!.body)) as { package_id: number; leverage: number }
      const created = accountRequestFixture({ id: 9, package_id: body.package_id, leverage: body.leverage })
      requests = [created, ...requests]
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/account-requests')) return jsonResponse(requests)
    const cancel = url.match(/\/investor\/account-requests\/(\d+)\/cancel$/)
    if (cancel && method === 'POST') {
      requests = requests.map((r) => r.id === Number(cancel[1]) ? { ...r, status: 'cancelled' as const } : r)
      return jsonResponse(requests[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const posts = (fetchMock: ReturnType<typeof mockRoutes>) =>
  fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')

function renderPage() {
  return render(<MemoryRouter><InvestorOpenAccount /></MemoryRouter>)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('an unverified investor is sent to verify first', async () => {
  mockRoutes({ kyc: 'submitted' })
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Open account' })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: 'Verify your identity first' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Go to Profile & verification' })).toHaveAttribute('href', '/org/1/invest/profile')
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
  expect(document.title).toBe('Open account · MirrorFleet')
})

test('a verified investor picks a package, generates passwords and requests with the MPIN', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: 'Choose Standard' }))
  expect(screen.getByText('1:100 · 1:200 · 1:500')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText('Leverage'), '200')
  await userEvent.click(screen.getByRole('button', { name: 'Generate main password' }))
  await userEvent.click(screen.getByRole('button', { name: 'Generate investor password' }))
  const main = (screen.getByLabelText('Main password') as HTMLInputElement).value
  const investor = (screen.getByLabelText('Investor password') as HTMLInputElement).value
  expect(main).toHaveLength(12)
  expect(screen.getByLabelText('Main password')).toHaveAttribute('type', 'text')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  const dialog = await screen.findByRole('dialog', { name: 'Request a Standard account at 1:200?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body))).toEqual({
    package_id: 1, leverage: 200, main_password: main, investor_password: investor, mpin: '123456',
  })
  expect(await screen.findByRole('heading', { name: 'Your request' })).toBeInTheDocument()
  expect(screen.getByText('Requested')).toBeInTheDocument()
})

test('a weak or repeated password is refused before anything is sent', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: 'Choose Standard' }))
  await userEvent.type(screen.getByLabelText('Main password'), 'short')
  await userEvent.type(screen.getByLabelText('Investor password'), 'Abcdefg1')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  expect(await screen.findByText(
    'Use 8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit')).toBeInTheDocument()
  await userEvent.clear(screen.getByLabelText('Main password'))
  await userEvent.type(screen.getByLabelText('Main password'), 'Abcdefg1')
  await userEvent.click(screen.getByRole('button', { name: 'Request account' }))
  expect(await screen.findByText('The investor password must differ from the main password')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('an open request can be cancelled, and the packages come back', async () => {
  const fetchMock = mockRoutes({ requests: [accountRequestFixture()] })
  renderPage()
  expect(await screen.findByRole('heading', { name: 'Your request' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel request' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel this request?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel request' }))
  expect(await screen.findByRole('button', { name: 'Choose Standard' })).toBeInTheDocument()
  expect(posts(fetchMock).map(([u]) => String(u))).toEqual(['/api/orgs/1/investor/account-requests/7/cancel'])
})

test('a fulfilled request shows the login and server, and another account can be requested under the cap', async () => {
  mockRoutes({ requests: [accountRequestFixture({ status: 'fulfilled', mt5_login: 5001, mt5_server: 'Broker-Live' })] })
  renderPage()
  expect(await screen.findByText('5001')).toBeInTheDocument()
  expect(screen.getByText('Broker-Live')).toBeInTheDocument()
  expect(screen.getByText('Ready')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel request' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Choose Standard' })).toBeInTheDocument()
})

test('a rejected request shows the note and offers the packages again', async () => {
  mockRoutes({ requests: [accountRequestFixture({ status: 'rejected', decision_note: 'Broker paused new accounts' })] })
  renderPage()
  expect(await screen.findByText('Your last request was rejected: Broker paused new accounts')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Choose Pro' })).toBeInTheDocument()
})

test('at the cap the page explains the limit instead of the packages', async () => {
  mockRoutes({ limit: { max: 2, used: 2 },
               requests: [accountRequestFixture({ status: 'fulfilled', mt5_login: 5001, mt5_server: 'Broker-Live' })] })
  renderPage()
  expect(await screen.findByRole('heading', { name: 'You have reached your account limit' })).toBeInTheDocument()
  expect(screen.getByText('This workspace allows 2 live accounts per investor. Ask your admin if you need another.'))
    .toBeInTheDocument()
  expect(screen.getByText('5001')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Choose Standard' })).not.toBeInTheDocument()
})
