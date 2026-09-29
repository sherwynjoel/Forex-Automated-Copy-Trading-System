import { renderHook, waitFor, act } from '@testing-library/react'
import { expect, test, vi, afterEach } from 'vitest'
import { draftOf, useAccountsPage, type SaveOutcome } from './useAccountsPage'
import { mockUseOrg } from '../../test/orgMock'
import { mt5Account } from '../../test/mt5Fixtures'
import type { Account } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const fleet: Account[] = [
  {
    ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
    enabled: true, multiplier: 1, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null, cutoff_date: null,
  },
  {
    ctid_trader_account_id: 2, trader_login: 12346, is_live: true, role: 'slave',
    enabled: true, multiplier: 2, status: 'ok', last_error: null,
    connection_status: 'active', nickname: 'Second desk', cutoff_date: '2026-12-01',
  },
]

const state = {
  accounts: { '1': { equity: 1049.48, open_pnl: 0, positions: [] } },
  master_positions: [], pending_orders: [], drift: [],
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Overrides match by substring in insertion order, method-qualified or not. */
function routes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [fragment, responder] of Object.entries(overrides)) {
      const [method, path] = fragment.includes(' ') ? fragment.split(' ') : [undefined, fragment]
      if (url.includes(path) && (!method || (init?.method || 'GET') === method)) return responder(init)
    }
    if (url.includes('/api/orgs/1/state')) return jsonResponse(state)
    if (url.includes('/api/orgs/1/accounts')) return jsonResponse(fleet)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function patchBodies(fetchMock: ReturnType<typeof routes>) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([u, init]) => [String(u), JSON.parse(String(init!.body))])
}

test('loads the accounts and the live equity, then stops loading', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes()
  const { result } = renderHook(() => useAccountsPage())

  expect(result.current.isLoading).toBe(true)
  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.accounts.map((a) => a.ctid_trader_account_id)).toEqual([1, 2])
  await waitFor(() => expect(result.current.equity['1']?.equity).toBe(1049.48))
  expect(result.current.error).toBeNull()
  expect(result.current.canControl).toBe(true)
})

test('a failed accounts load surfaces the server message and still stops loading', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes({ '/api/orgs/1/accounts': () => jsonResponse({ detail: 'boom' }, 500) })
  const { result } = renderHook(() => useAccountsPage())

  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.error).toBe('500: boom')
  expect(result.current.accounts).toEqual([])
})

test('flatten: the dialog closes at once, the kill switch is POSTed for that account, and the row is marked done', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({
      status: 'flattened', paused: false,
      accounts: [{ account_id: 2, positions_closed: 3, orders_cancelled: 1, positions_remaining: [], error: null }],
    }),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const follower = result.current.accounts[1]

  act(() => result.current.askFlatten(follower))
  expect(result.current.dialog).toEqual({ kind: 'flatten', account: follower })

  await act(async () => { await result.current.confirmFlatten() })

  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/orgs/1/control/close-all')
  expect(call).toBeTruthy()
  expect(JSON.parse(String(call![1]!.body))).toEqual({ account_id: 2 })
  expect(result.current.dialog).toBeNull()
  expect(result.current.flatten[2]).toBe('done')
  expect(result.current.notice).toBe(
    'Closed 3 positions and cancelled 1 order on account 12346. Verified flat.')
})

test('flatten that leaves a position open is an error on the row, never done', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({
      status: 'flattened', paused: false,
      accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, positions_remaining: [77], error: null }],
    }),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))

  act(() => result.current.askFlatten(result.current.accounts[1]))
  await act(async () => { await result.current.confirmFlatten() })

  expect(result.current.flatten[2]).toBe('error')
  expect(result.current.error).toBe(
    'Account 12346: closed 1, but 1 position could not be closed. ' +
    'You are still exposed — close it in the platform.')
})

test('remove: confirming DELETEs the MT5 account, announces it, and reloads the list', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  let listed: Account[] = [...fleet, mt5Account]
  const fetchMock = routes({
    'DELETE /api/orgs/1/mt5/accounts/1000000000001': () => {
      listed = fleet
      return new Response(null, { status: 204 })
    },
    '/api/orgs/1/accounts': () => jsonResponse(listed),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(3))

  act(() => result.current.askRemove(result.current.accounts[2]))
  expect(result.current.dialog?.kind).toBe('remove')
  await act(async () => { await result.current.confirmRemove() })

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/orgs/1/mt5/accounts/1000000000001', expect.objectContaining({ method: 'DELETE' }))
  expect(result.current.dialog).toBeNull()
  expect(result.current.notice).toBe('Account removed. Its terminal key stops working immediately.')
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
})

test('saveEdits sends one PATCH per changed field, with the single-key bodies the blur-save sent', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes()
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const master = result.current.accounts[0]

  await act(async () => {
    await result.current.saveEdits(master, {
      ...draftOf(master), nickname: 'Main live', cutoff_date: '2026-09-16', enabled: false,
    })
  })

  expect(patchBodies(fetchMock)).toEqual([
    ['/api/orgs/1/accounts/1', { nickname: 'Main live' }],
    ['/api/orgs/1/accounts/1', { cutoff_date: '2026-09-16' }],
    ['/api/orgs/1/accounts/1', { enabled: false }],
  ])
  expect(result.current.pending.has(1)).toBe(false)
})

test('saveEdits to master waits for the promote confirmation; cancelling sends nothing', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes()
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const follower = result.current.accounts[1]

  let outcome!: Promise<SaveOutcome>
  act(() => { outcome = result.current.saveEdits(follower, { ...draftOf(follower), role: 'master' }) })
  await waitFor(() => expect(result.current.dialog).toEqual({ kind: 'promote', account: follower }))

  await act(async () => { result.current.resolvePromote(false) })
  await expect(outcome).resolves.toEqual({ promoteCancelled: true })
  expect(patchBodies(fetchMock)).toEqual([])
  expect(result.current.dialog).toBeNull()
})
