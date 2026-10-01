import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import AccountRequestsTab from './AccountRequestsTab'
import { accountRequestFixture } from '../../test/portalFixtures'
import type { Account } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const mt5 = { ctid_trader_account_id: 2001, trader_login: 0, is_live: true, role: 'slave', enabled: true,
  multiplier: 1, status: 'ok', connection_status: 'active', nickname: 'Ada MT5', platform: 'mt5',
  mt5: { login: 5001, broker: 'Broker', server: 'Broker-Live', currency: 'USD', hedging: true,
         trade_mode: 'real', ea_version: '1', last_seen_at: null, connected: true } } as Account
const master = { ...mt5, ctid_trader_account_id: 100, role: 'master', nickname: 'Master' } as Account

function mockRoutes() {
  let rows = [accountRequestFixture({ email: 'ada@example.com', display_name: 'Ada' })]
  let reveals = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/account-requests')) return jsonResponse(rows)
    if (url.endsWith('/accounts')) return jsonResponse([mt5, master])
    if (url.endsWith('/account-requests/7/reveal')) {
      reveals += 1
      if (reveals === 1) return jsonResponse({ detail: 'Invalid MPIN', attempts_left: 4 }, 401)
      return jsonResponse({ main_password: 'Main1234', investor_password: 'Look1234' })
    }
    const m = url.match(/\/account-requests\/7\/(fulfil|reject)$/)
    if (m) {
      rows = rows.map((r) => ({ ...r, status: m[1] === 'fulfil' ? 'fulfilled' as const : 'rejected' as const }))
      return jsonResponse(rows[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof mockRoutes>, tail: string) =>
  JSON.parse(String((fetchMock.mock.calls.find(([u]) => String(u).endsWith(tail))![1] as RequestInit).body))

async function enterPin(scope: HTMLElement, pin: string) {
  await userEvent.click(within(scope).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('reveal with the MPIN, then fulfil with login, server and a linked MT5 account', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={onDone} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.getByText('Standard')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await enterPin(drawer, '000000')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Wrong MPIN, 4 tries left')).toBeInTheDocument()
  await enterPin(drawer, '123456')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Main1234')).toBeInTheDocument()
  expect(within(drawer).getByText('Look1234')).toBeInTheDocument()
  const options = within(within(drawer).getByLabelText('Link to account')).getAllByRole('option')
  expect(options.map((o) => o.textContent)).toEqual(['Do not link', 'Ada MT5 (MT5 5001)'])
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '5001')
  await userEvent.type(within(drawer).getByLabelText('MT5 server'), 'Broker-Live')
  await userEvent.selectOptions(within(drawer).getByLabelText('Link to account'), '2001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Account request #7 fulfilled'))
  expect(bodyOf(fetchMock, '/reveal')).toEqual({ mpin: '000000' })
  expect(bodyOf(fetchMock, '/fulfil')).toEqual({ mt5_login: 5001, mt5_server: 'Broker-Live', account_id: 2001, note: null })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(await screen.findByText('No open account requests')).toBeInTheDocument()
})

test('fulfil needs a login number and a server before anything is sent', async () => {
  const fetchMock = mockRoutes()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Enter the MT5 login number')).toBeInTheDocument()
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '5001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Enter the MT5 server')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/fulfil'))).toBe(false)
})

test('a rejection needs a note and posts it', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={onDone} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Reject account request 7' }))
  const dialog = await screen.findByRole('dialog', { name: 'Reject the account request of Ada' })
  expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled()
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Broker paused new accounts')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Account request #7 rejected'))
  expect(bodyOf(fetchMock, '/reject')).toEqual({ note: 'Broker paused new accounts' })
})

test('a viewer sees the queue but no actions', async () => {
  mockRoutes()
  render(<AccountRequestsTab orgId={1} control={false} show="open" onDone={vi.fn()} onError={vi.fn()} />)
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Fulfil account request 7' })).not.toBeInTheDocument()
})

test('a failed load stays in the tab with Retry, never an empty queue', async () => {
  let fail = true
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/account-requests')) {
      if (fail) return jsonResponse({ detail: 'boom' }, 500)
      return jsonResponse([accountRequestFixture({ email: 'ada@example.com', display_name: 'Ada' })])
    }
    return jsonResponse([])
  }))
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  expect(await screen.findByText('boom')).toBeInTheDocument()
  expect(screen.queryByText('No open account requests')).not.toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Ada')).toBeInTheDocument()
  expect(screen.queryByText('boom')).not.toBeInTheDocument()
})

test('a fulfil 409 clears the passwords and reloads the queue', async () => {
  let decided = false
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/account-requests')) {
      return jsonResponse([accountRequestFixture({ display_name: 'Ada', ...(decided ? { status: 'rejected' as const } : {}) })])
    }
    if (url.endsWith('/reveal')) return jsonResponse({ main_password: 'Main1234', investor_password: 'Look1234' })
    if (url.endsWith('/fulfil')) { decided = true; return jsonResponse({ detail: 'Request already decided' }, 409) }
    return jsonResponse([])
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await enterPin(drawer, '123456')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Main1234')).toBeInTheDocument()
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '5001')
  await userEvent.type(within(drawer).getByLabelText('MT5 server'), 'Broker-Live')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Request already decided')).toBeInTheDocument()
  expect(within(drawer).queryByText('Main1234')).not.toBeInTheDocument()
  expect(await screen.findByText('No open account requests')).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/account-requests'))).toHaveLength(2)
})

test('a login beyond the safe integer range is refused inline', async () => {
  const fetchMock = mockRoutes()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Fulfil account request 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await userEvent.type(within(drawer).getByLabelText('MT5 login'), '9007199254740993')
  await userEvent.type(within(drawer).getByLabelText('MT5 server'), 'Broker-Live')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Fulfil request' }))
  expect(await within(drawer).findByText('Enter the MT5 login number')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/fulfil'))).toBe(false)
})

test('closing the drawer forgets the passwords; reopening asks for the MPIN again', async () => {
  mockRoutes()
  render(<AccountRequestsTab orgId={1} control show="open" onDone={vi.fn()} onError={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Fulfil account request 7' }))
  let drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  await enterPin(drawer, '000000')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  await within(drawer).findByText('Wrong MPIN, 4 tries left')
  await enterPin(drawer, '123456')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Reveal passwords' }))
  expect(await within(drawer).findByText('Main1234')).toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: 'Fulfil account request 7' }))
  drawer = await screen.findByRole('dialog', { name: 'Fulfil request #7' })
  expect(within(drawer).queryByText('Main1234')).not.toBeInTheDocument()
  expect(within(drawer).getByRole('button', { name: 'Reveal passwords' })).toBeDisabled()
  expect(within(drawer).getByLabelText('Your MPIN digit 1 of 6')).toHaveValue('')
})
