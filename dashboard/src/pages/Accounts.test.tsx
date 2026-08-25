import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import Accounts from './Accounts'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function setRole(role: Role) {
  useOrgMock.mockReturnValue(mockUseOrg(role))
}

let mockWindowOpen: ReturnType<typeof vi.fn>
let mockLocationAssign: ReturnType<typeof vi.fn>
let focusListeners: Set<(event: Event) => void> = new Set()

beforeEach(() => {
  mockWindowOpen = vi.fn()
  mockLocationAssign = vi.fn()
  // jsdom's location is not writable; stub only assign, which is what the
  // connect handler calls.
  Object.defineProperty(window, 'location', {
    value: { ...window.location, assign: mockLocationAssign },
    writable: true,
    configurable: true,
  })
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
    cutoff_date: null,
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
    cutoff_date: '2026-12-01',
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
// Live equity comes from the engine's state endpoint, keyed by account id,
// NOT from the accounts row -- the accounts table stores no balance.
const mockState = {
  accounts: {
    // Keyed by ctid_trader_account_id (1), NOT the trader login shown on screen.
    '1': { equity: 1049.48, open_pnl: 0, positions: [] },
    // Account 2 deliberately absent: an account the engine has no reading
    // for must show a dash, never 0.00, which would read as "empty account".
  },
  master_positions: [],
  pending_orders: [],
  drift: [],
}

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
    if (url.includes('/api/orgs/1/state')) return jsonResponse(mockState)
    if (url.includes('/api/orgs/1/accounts')) return jsonResponse(mockAccounts)
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
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  expect(screen.getByDisplayValue('Second desk')).toBeInTheDocument()
})

test('connect navigates THIS tab to the org-scoped route, never a popup', async () => {
  // A popup breaks the flow outright: the broker's redirect back to
  // /api/oauth/callback is cross-site, and a SameSite=Lax session cookie
  // is withheld from a popup navigated that way -- the callback answered
  // "Not authenticated" and no account could ever be connected.
  setRole('admin')
  mockRoutes()
  renderAccounts()

  const connectButton = await screen.findByRole('button', { name: /connect ctrader id/i })
  await userEvent.click(connectButton)

  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
  expect(mockWindowOpen).not.toHaveBeenCalled()
})

test('window-focus refetch: refetches accounts after OAuth popup closes', async () => {
  setRole('admin')
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
    const accountCalls = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/orgs/1/accounts')
    expect(accountCalls.length).toBeGreaterThanOrEqual(2)
  })
})

test('role select PATCHes role', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const masterSelect = screen.getByLabelText(/role for account 12345/i)
  await userEvent.selectOptions(masterSelect, 'slave')

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({ method: 'PATCH' })
    )
  })
})

test('choosing Master confirms, then promotes (old master demoted server-side)', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const slaveSelect = screen.getByLabelText(/role for account 12346/i)
  await userEvent.selectOptions(slaveSelect, 'master')

  // No PATCH yet: promoting a master re-shapes the whole fleet, so it asks.
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/becomes a slave/i)
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /make it the master/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ role: 'master' })
  })
})

test('cancelling the master confirmation changes nothing', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await userEvent.selectOptions(screen.getByLabelText(/role for account 12346/i), 'master')
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')).toBe(false)
  expect((screen.getByLabelText(/role for account 12346/i) as HTMLSelectElement).value).toBe('slave')
})

test('enabled toggle PATCHes enabled field (not role)', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const checkbox = screen.getByLabelText(/copying enabled for account 12345/i)
  await userEvent.click(checkbox)

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
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
  setRole('admin')
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
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ nickname: 'Main live' }),
      })
    )
  })
})

test('cutoff date edit PATCHes cutoff_date', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const cutoffInput = screen.getByLabelText(/cutoff date for account 12345/i)
  fireEvent.change(cutoffInput, { target: { value: '2026-09-16' } })
  fireEvent.blur(cutoffInput)

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cutoff_date: '2026-09-16' }),
      })
    )
  })
})

test('clearing the cutoff date PATCHes an empty cutoff_date', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const cutoffInput = screen.getByLabelText(/cutoff date for account 12346/i)
  expect((cutoffInput as HTMLInputElement).value).toBe('2026-12-01')
  fireEvent.change(cutoffInput, { target: { value: '' } })
  fireEvent.blur(cutoffInput)

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/2',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cutoff_date: '' }),
      })
    )
  })
})

test('viewer sees the cutoff date read-only', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  expect(screen.getByText('2026-12-01')).toBeInTheDocument()
  expect(screen.queryByLabelText(/cutoff date for account/i)).not.toBeInTheDocument()
})

test('disconnect confirms then DELETEs the ACCOUNT-scoped connection route', async () => {
  setRole('admin')
  const fetchMock = mockRoutes({
    'DELETE /api/orgs/1/accounts/2/connection': () =>
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
      '/api/orgs/1/accounts/2/connection',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})

test('details button opens the drawer with broker profile fields', async () => {
  setRole('admin')
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
  setRole('admin')
  const fetchMock = mockRoutes({
    'POST /api/orgs/1/control/close-all': () =>
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
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/api/orgs/1/control/close-all'))
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ account_id: 2 })
  })
  // Outcome notice
  expect(await screen.findByText(/closed 3 position/i)).toBeInTheDocument()
})

test('flatten closes the dialog immediately and marks the row button Flattening… while in flight', async () => {
  setRole('admin')
  let resolveCloseAll!: (value: Response) => void
  const pendingCloseAll = new Promise<Response>((res) => { resolveCloseAll = res })
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => pendingCloseAll as unknown as Response,
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const flattenButtons = screen.getAllByRole('button', { name: /^flatten$/i })
  await userEvent.click(flattenButtons[flattenButtons.length - 1])
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  // The dialog goes away at once; progress lives on the row button instead.
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  const busyButton = screen.getByRole('button', { name: /flattening/i })
  // aria-disabled (not disabled) keeps the button focusable so keyboard focus
  // is not stranded when the dialog closes.
  expect(busyButton).toHaveAttribute('aria-disabled', 'true')
  // The other row's button is untouched.
  expect(screen.getAllByRole('button', { name: /^flatten$/i })).toHaveLength(1)

  resolveCloseAll(jsonResponse({
    status: 'flattened', paused: false,
    accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, error: null }],
  }))
  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The outcome notice is announced to assistive tech and names the account.
  expect(screen.getByRole('status')).toHaveTextContent(/on account 12346/i)
})

test('the Flattened ✓ confirmation reverts to Flatten after a few seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    setRole('admin')
    mockRoutes({
      'POST /api/orgs/1/control/close-all': () =>
        jsonResponse({
          status: 'flattened', paused: false,
          accounts: [{ account_id: 2, positions_closed: 2, orders_cancelled: 0, error: null }],
        }),
    })
    renderAccounts()

    await waitFor(() => {
      expect(screen.getByText('12346')).toBeInTheDocument()
    })

    const flattenButtons = screen.getAllByRole('button', { name: /^flatten$/i })
    await userEvent.click(flattenButtons[flattenButtons.length - 1])
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))
    expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.queryByRole('button', { name: /flattened/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^flatten$/i })).toHaveLength(2)
  } finally {
    vi.useRealTimers()
  }
})

test('the outcome live region is mounted before any flatten so announcements fire', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  // A live region only announces reliably if it exists BEFORE its text changes.
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
})

test('a successful retry clears the earlier flatten failure banner', async () => {
  setRole('admin')
  let closeAllCalls = 0
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => {
      closeAllCalls += 1
      return closeAllCalls === 1
        ? jsonResponse({ detail: 'copier unreachable' }, 502)
        : jsonResponse({
            status: 'flattened', paused: false,
            accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, error: null }],
          })
    },
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const flattenButtons = screen.getAllByRole('button', { name: /^flatten$/i })
  await userEvent.click(flattenButtons[flattenButtons.length - 1])
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  const retryButton = await screen.findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  await userEvent.click(retryButton)
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The stale failure alert must not contradict the fresh success notice.
  expect(screen.queryByText(/flatten failed on account 12346/i)).not.toBeInTheDocument()
})

test('flatten failure flags the row button and names the account in the error', async () => {
  setRole('admin')
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({ detail: 'copier unreachable' }, 502),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const flattenButtons = screen.getAllByRole('button', { name: /^flatten$/i })
  await userEvent.click(flattenButtons[flattenButtons.length - 1])
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  const retryButton = await screen.findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  // The failed button is a retry: clicking it reopens the confirmation.
  await userEvent.click(retryButton)
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
})

// ---------- Task 17: role gating ----------

test('viewer (below control) gets read-only rows: no editors, no disconnect, no connect link', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // Role, enabled and nickname editors are all gone.
  expect(screen.queryByLabelText(/role for account/i)).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/multiplier for account/i)).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/copying enabled for account/i)).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/nickname for account/i)).not.toBeInTheDocument()
  // ...but the read-only values are still shown.
  expect(screen.getByText('master')).toBeInTheDocument()
  expect(screen.getByText('slave')).toBeInTheDocument()
  expect(screen.getByText('Second desk')).toBeInTheDocument()

  expect(screen.queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
  // Flatten and Re-grant access are the same destructive/OAuth class as the
  // gated controls above and must be hidden below control too.
  expect(screen.queryByRole('button', { name: /^flatten$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /re-grant access/i })).not.toBeInTheDocument()
})

test('trader (below control) also gets read-only rows and no connect link', async () => {
  setRole('trader')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  expect(screen.queryByLabelText(/role for account/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^flatten$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /re-grant access/i })).not.toBeInTheDocument()
})

test('admin (control) sees editors, disconnect, the connect link, flatten, and re-grant access', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  expect(screen.getByLabelText(/role for account 12345/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/copying enabled for account 12345/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/nickname for account 12345/i)).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: /disconnect/i }).length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: /connect ctrader id/i })).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: /^flatten$/i }).length).toBeGreaterThan(0)
  expect(screen.getAllByRole('button', { name: /re-grant access/i }).length).toBeGreaterThan(0)
})


test('shows each account\'s live equity, and a dash when the engine has no reading', async () => {
  // Operators asked for per-account equity on this screen: the header only
  // ever showed the MASTER's, so there was no way to see at a glance that a
  // slave had drifted far from the others, or been drained by a margin call.
  setRole('admin')
  mockRoutes()
  renderAccounts()

  expect(await screen.findByText('1,049.48')).toBeInTheDocument()

  // The account with no engine reading must not be rendered as 0.00.
  const rows = screen.getAllByRole('row')
  const unknown = rows.find((r) => r.textContent?.includes('12346'))
  expect(unknown).toBeTruthy()
  expect(unknown!.textContent).not.toMatch(/0\.00/)
})

test('equity failure does not break the accounts list', async () => {
  // The account list is the point of this page. If the engine is down the
  // rows must still render -- an unreachable copier must not blank the
  // screen an operator uses to disconnect or flatten an account.
  setRole('admin')
  mockRoutes({
    '/api/orgs/1/state': () => new Response('boom', { status: 502 }),
  })
  renderAccounts()

  expect(await screen.findByText('12345')).toBeInTheDocument()
  expect(screen.getByText('12346')).toBeInTheDocument()
})
