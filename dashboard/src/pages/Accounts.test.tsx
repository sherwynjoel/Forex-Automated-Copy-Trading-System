import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import Accounts from './Accounts'

let mockWindowOpen: ReturnType<typeof vi.fn>
let focusListeners: Set<(event: Event) => void> = new Set()

beforeEach(() => {
  mockWindowOpen = vi.fn()
  focusListeners.clear()

  Object.defineProperty(window, 'open', {
    value: mockWindowOpen,
    writable: true,
  })

  // Capture focus event listeners
  const originalAddEventListener = window.addEventListener
  vi.spyOn(window, 'addEventListener').mockImplementation((event: string, handler: EventListenerOrEventListenerObject) => {
    if (event === 'focus' && typeof handler === 'function') {
      focusListeners.add(handler as (event: Event) => void)
    }
    return originalAddEventListener.call(window, event, handler)
  })
})

afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  focusListeners.clear()
})

const mockAccounts = [
  {
    ctid_trader_account_id: 1,
    trader_login: 12345,
    is_live: false,
    role: 'master',
    enabled: true,
    multiplier: 1.0,
    status: 'ok',
    last_error: null,
    connection_status: 'active',
    nickname: null,
  },
  {
    ctid_trader_account_id: 2,
    trader_login: 12346,
    is_live: true,
    role: 'slave',
    enabled: true,
    multiplier: 2.0,
    status: 'ok',
    last_error: null,
    connection_status: 'active',
    nickname: 'Second desk',
  },
]

const mockDetails = {
  account_id: 1, trader_login: 12345, balance: 10000, deposit_currency: 'USD',
  leverage: 50, max_leverage: 500, broker_name: 'FP Markets',
  registration_timestamp: 1700000000000, account_type: 'HEDGED',
  access_rights: 'FULL_ACCESS', swap_free: false, is_limited_risk: false,
  open_positions: [], pending_orders: [],
  nickname: null, role: 'master', enabled: true, multiplier: 1, status: 'ok',
  last_error: null, is_live: false,
  connection: {
    granted_at: '2026-08-01T10:00:00+00:00',
    expires_at: '2026-08-31T10:00:00+00:00',
    status: 'active', scope: 'trading',
  },
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Route-based fetch mock; individual tests override specific routes. */
function mockRoutes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [fragment, responder] of Object.entries(overrides)) {
      const [method, path] = fragment.includes(' ')
        ? fragment.split(' ')
        : [undefined, fragment]
      if (url.includes(path) && (!method || (init?.method || 'GET') === method)) {
        return responder(init)
      }
    }
    if (url.includes('/details')) return jsonResponse(mockDetails)
    if (url.includes('/api/accounts')) return jsonResponse(mockAccounts)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAccounts() {
  return render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )
}

test('loads and displays accounts with nicknames on mount', async () => {
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  expect(screen.getByDisplayValue('Second desk')).toBeInTheDocument()
})

test('connect button opens oauth popup', async () => {
  mockRoutes()
  renderAccounts()

  const connectButton = await screen.findByRole('button', { name: /connect ctrader id/i })
  await userEvent.click(connectButton)

  expect(mockWindowOpen).toHaveBeenCalledWith(
    '/api/oauth/connect',
    'ctrader-oauth',
    'width=520,height=680'
  )
})

test('window-focus refetch: refetches accounts after OAuth popup closes', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })
  const callsBefore = fetchMock.mock.calls.length

  act(() => {
    focusListeners.forEach((listener) => listener(new Event('focus')))
  })

  await waitFor(() => {
    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore)
    const accountCalls = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/accounts')
    expect(accountCalls.length).toBeGreaterThanOrEqual(2)
  })
})

test('role select PATCHes role', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const masterSelect = screen.getByLabelText(/role for account 12345/i)
  await userEvent.selectOptions(masterSelect, 'slave')

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/1',
      expect.objectContaining({ method: 'PATCH' })
    )
  })
})

test('409 on second master shows inline error', async () => {
  mockRoutes({
    'PATCH /api/accounts/2': () => new Response('Conflict', { status: 409 }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const slaveSelect = screen.getByLabelText(/role for account 12346/i)
  await userEvent.selectOptions(slaveSelect, 'master')

  await waitFor(() => {
    expect(screen.getByText(/a master already exists/i)).toBeInTheDocument()
  })
})

test('multiplier edit PATCHes multiplier', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const multiplierInput = screen.getByLabelText(/multiplier for account 12346/i)
  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '3.5')
  await userEvent.tab()

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/2',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ multiplier: 3.5 }),
      })
    )
  })
})

test('multiplier validation: rejects values <= 0', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const multiplierInput = screen.getByLabelText(/multiplier for account 12346/i)
  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '0')
  await userEvent.tab()

  await waitFor(() => {
    expect(screen.getByText(/multiplier must be greater than 0/i)).toBeInTheDocument()
  })
  expect(
    fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'PATCH')
  ).toBe(false)
})

test('multiplier 400 error shows inline error', async () => {
  mockRoutes({
    'PATCH /api/accounts/2': () => new Response('Invalid multiplier', { status: 400 }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const multiplierInput = screen.getByLabelText(/multiplier for account 12346/i)
  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '5.0')
  await userEvent.tab()

  await waitFor(() => {
    expect(screen.getByText(/failed to update multiplier/i)).toBeInTheDocument()
  })
})

test('enabled toggle PATCHes enabled field (not role)', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const checkbox = screen.getByLabelText(/copying enabled for account 12345/i)
  await userEvent.click(checkbox)

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    )
  })

  const patchCalls = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === 'PATCH')
  expect(patchCalls).toHaveLength(1)
  expect(String(patchCalls[0][1]!.body)).not.toContain('role')
})

test('nickname edit PATCHes nickname', async () => {
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const nicknameInput = screen.getByLabelText(/nickname for account 12345/i)
  await userEvent.type(nicknameInput, 'Main live')
  await userEvent.tab()

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ nickname: 'Main live' }),
      })
    )
  })
})

test('disconnect confirms then DELETEs the ACCOUNT-scoped connection route', async () => {
  const fetchMock = mockRoutes({
    'DELETE /api/accounts/2/connection': () =>
      jsonResponse({ detail: 'ok', accounts_removed: 2, copier_reloaded: true }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const disconnectButtons = screen.getAllByRole('button', { name: /disconnect/i })
  await userEvent.click(disconnectButtons[disconnectButtons.length - 1])

  // ConfirmDialog explains the whole-grant consequence, then confirms.
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(/every account under/i)).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: /disconnect grant/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/2/connection',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})

test('details button opens the drawer with broker profile fields', async () => {
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const detailButtons = screen.getAllByRole('button', { name: /details/i })
  await userEvent.click(detailButtons[0])

  expect(await screen.findByText('FP Markets')).toBeInTheDocument()
  expect(screen.getByText('1:50')).toBeInTheDocument()
  expect(screen.getByText('HEDGED')).toBeInTheDocument()
  expect(screen.getByText('USD')).toBeInTheDocument()
  // Grant info from the DB side of the merge
  expect(screen.getByText('OAuth grant')).toBeInTheDocument()
})

test('flatten button confirms then POSTs the per-account kill switch', async () => {
  const fetchMock = mockRoutes({
    'POST /api/control/close-all': () =>
      jsonResponse({
        status: 'flattened', paused: false,
        accounts: [{ account_id: 2, positions_closed: 3, orders_cancelled: 1, error: null }],
      }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const flattenButtons = screen.getAllByRole('button', { name: /flatten/i })
  await userEvent.click(flattenButtons[flattenButtons.length - 1])

  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/api/control/close-all'))
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ account_id: 2 })
  })
  // Outcome notice
  expect(await screen.findByText(/closed 3 position/i)).toBeInTheDocument()
})
