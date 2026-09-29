import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import Accounts from './Accounts'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'
import { mt5Account } from '../test/mt5Fixtures'

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

/** The fleet plus one connected MT5 account. `extra` is spread FIRST: the
 *  override loop matches by substring in insertion order, and
 *  '/api/orgs/1/accounts' would otherwise swallow '/accounts/…/details'. */
function mockMt5Routes(extra: Record<string, (init?: RequestInit) => Response> = {}) {
  return mockRoutes({
    ...extra,
    '/api/orgs/1/accounts': () => jsonResponse([...mockAccounts, mt5Account]),
  })
}

/** The table row whose text contains `text` (a login, a nickname, a broker). */
function rowFor(text: string): HTMLElement {
  const row = screen.getAllByRole('row').find((r) => r.textContent?.includes(text))
  if (!row) throw new Error(`no row contains ${text}`)
  return row
}

/** Opens the row's one actions menu and returns it. */
async function openMenu(text: string): Promise<HTMLElement> {
  await userEvent.click(within(rowFor(text)).getByRole('button', { name: /^actions for/i }))
  return screen.findByRole('menu')
}

async function chooseFromMenu(text: string, item: RegExp) {
  const menu = await openMenu(text)
  await userEvent.click(within(menu).getByRole('menuitem', { name: item }))
}

function menuItemNames(menu: HTMLElement): string[] {
  return within(menu).getAllByRole('menuitem').map((el) => el.textContent?.trim() ?? '')
}

/** Opens the row's Details drawer -- the only dialog on screen at that point. */
async function openDrawer(text: string): Promise<HTMLElement> {
  await userEvent.click(within(rowFor(text)).getByRole('button', { name: /^details$/i }))
  return screen.findByRole('dialog')
}

function patchCalls(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
}

test('loads and displays accounts with nicknames on mount', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  expect(screen.getByText('Second desk')).toBeInTheDocument()
})

test('the page sets the document title', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  await waitFor(() => expect(document.title).toBe('Accounts · MirrorFleet'))
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

test('rows are read-only: no text field, select or checkbox inside the table', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  const table = await screen.findByRole('table')
  await within(table).findByText('12345')
  expect(within(table).queryAllByRole('textbox')).toHaveLength(0)
  expect(within(table).queryAllByRole('combobox')).toHaveLength(0)
  expect(within(table).queryAllByRole('checkbox')).toHaveLength(0)
})

// ---------- The drawer's edit form ----------

test('role select in the drawer PATCHes role on Save changes', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'slave')
  // Nothing is sent until Save: the old select saved on change.
  expect(patchCalls(fetchMock)).toHaveLength(0)
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ role: 'slave' }) })
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

  const drawer = await openDrawer('12346')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'master')
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  // No PATCH yet: promoting a master re-shapes the whole fleet, so it asks.
  const dialog = await screen.findByRole('dialog', { name: /make account 12346 the master/i })
  expect(dialog).toHaveTextContent(/becomes a follower/i)
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

  const drawer = await openDrawer('12346')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'master')
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))
  const dialog = await screen.findByRole('dialog', { name: /make account 12346 the master/i })
  await userEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }))

  expect(screen.queryByRole('dialog', { name: /make account 12346 the master/i })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')).toBe(false)
  await waitFor(() => {
    expect((within(drawer).getByLabelText('Role') as HTMLSelectElement).value).toBe('slave')
  })
})

test('enabled toggle PATCHes enabled field (not role)', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.click(within(drawer).getByLabelText('Copying enabled'))
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    )
  })

  const patches = patchCalls(fetchMock)
  expect(patches).toHaveLength(1)
  expect(String(patches[0][1]!.body)).not.toContain('role')
})

test('nickname edit PATCHes nickname on Save changes, the same body the blur-save sent', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  // Leaving the field no longer saves: only Save does.
  await userEvent.tab()
  expect(patchCalls(fetchMock)).toHaveLength(0)
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

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

  const drawer = await openDrawer('12345')
  fireEvent.change(within(drawer).getByLabelText('Cutoff date'), { target: { value: '2026-09-16' } })
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

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

  const drawer = await openDrawer('12346')
  const cutoffInput = within(drawer).getByLabelText('Cutoff date')
  expect((cutoffInput as HTMLInputElement).value).toBe('2026-12-01')
  fireEvent.change(cutoffInput, { target: { value: '' } })
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

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

test('one Save with several changes sends one PATCH per field, each with its old single-key body', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  fireEvent.change(within(drawer).getByLabelText('Cutoff date'), { target: { value: '2026-09-16' } })
  await userEvent.click(within(drawer).getByLabelText('Copying enabled'))
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(patchCalls(fetchMock).map(([u, init]) => [String(u), String(init!.body)])).toEqual([
      ['/api/orgs/1/accounts/1', JSON.stringify({ nickname: 'Main live' })],
      ['/api/orgs/1/accounts/1', JSON.stringify({ cutoff_date: '2026-09-16' })],
      ['/api/orgs/1/accounts/1', JSON.stringify({ enabled: false })],
    ])
  })
})

test('a Save that the server partly refuses names the failed field in the drawer and keeps what was typed', async () => {
  setRole('admin')
  let listed = mockAccounts.map((a) => ({ ...a }))
  mockRoutes({
    // Before the list route, which would otherwise swallow '/accounts/1/details'.
    '/details': () => jsonResponse(mockDetails),
    'PATCH /api/orgs/1/accounts/1': (init) => {
      const body = JSON.parse(String(init?.body))
      if ('enabled' in body) return jsonResponse({ detail: 'boom' }, 500)
      listed = listed.map((a) => (a.ctid_trader_account_id === 1 ? { ...a, ...body } : a))
      return jsonResponse({})
    },
    'GET /api/orgs/1/accounts': () => jsonResponse(listed),
  })
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  const enabled = within(drawer).getByLabelText('Copying enabled') as HTMLInputElement
  await userEvent.click(enabled)
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  // The alert is inside the drawer, not behind it on the page.
  const alert = await within(drawer).findByRole('alert')
  expect(alert).toHaveTextContent(/copying enabled: 500: boom/i)
  expect(alert).not.toHaveTextContent(/nickname/i)
  // The refused field keeps the typed value; the saved one shows the server's.
  expect(enabled.checked).toBe(false)
  expect((within(drawer).getByLabelText('Nickname') as HTMLInputElement).value).toBe('Main live')
  expect(within(rowFor('12345')).getByText('Main live')).toBeInTheDocument()
  // The form stays open with the failed change still pending a retry.
  expect(within(drawer).getByRole('button', { name: /save changes/i })).toBeEnabled()
})

test('a Save where every field succeeds shows no alert in the drawer', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  await userEvent.click(within(drawer).getByLabelText('Copying enabled'))
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => expect(patchCalls(fetchMock)).toHaveLength(2))
  await waitFor(() => expect(within(drawer).getByLabelText('Nickname')).toBeEnabled())
  expect(within(drawer).queryByRole('alert')).not.toBeInTheDocument()
})

test('Cancel discards the drawer edits and sends nothing', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  const nickname = within(drawer).getByLabelText('Nickname') as HTMLInputElement
  const save = within(drawer).getByRole('button', { name: /save changes/i })
  expect(save).toBeDisabled()

  await userEvent.type(nickname, 'Scratch')
  expect(save).toBeEnabled()
  await userEvent.click(within(drawer).getByRole('button', { name: /^cancel$/i }))

  expect(nickname.value).toBe('')
  expect(save).toBeDisabled()
  expect(patchCalls(fetchMock)).toHaveLength(0)
})

test('viewer sees the cutoff date read-only', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12346')
  expect(within(drawer).getByText('2026-12-01')).toBeInTheDocument()
  expect(within(drawer).queryByLabelText(/cutoff date/i)).not.toBeInTheDocument()
})

// ---------- Row menu actions ----------

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

  await chooseFromMenu('12346', /^disconnect$/i)

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

test("Escape closes the details drawer and returns focus to the row's Details button", async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const detailsButton = screen.getAllByRole('button', { name: /details/i })[0]
  await userEvent.click(detailsButton)

  const drawer = await screen.findByRole('dialog', { name: /account 12345/i })
  expect(within(drawer).getByRole('button', { name: /^close$/i })).toBeInTheDocument()

  await userEvent.keyboard('{Escape}')

  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /account 12345/i })).not.toBeInTheDocument()
  })
  expect(detailsButton).toHaveFocus()
})

test('flatten confirms then POSTs the per-account kill switch', async () => {
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

  await chooseFromMenu('12346', /^flatten$/i)

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

test('flatten closes the dialog immediately and marks the row Flattening… while in flight', async () => {
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

  await chooseFromMenu('12346', /^flatten$/i)
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  // The dialog goes away at once; progress lives on the row instead.
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  const busyButton = within(rowFor('12346')).getByRole('button', { name: /flattening/i })
  // aria-disabled (not disabled) keeps the button focusable so keyboard focus
  // is not stranded when the dialog closes.
  expect(busyButton).toHaveAttribute('aria-disabled', 'true')
  // The other row is untouched.
  expect(within(rowFor('12345')).queryByRole('button', { name: /flatten/i })).not.toBeInTheDocument()

  resolveCloseAll(jsonResponse({
    status: 'flattened', paused: false,
    accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, error: null }],
  }))
  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The outcome notice is announced to assistive tech and names the account.
  expect(screen.getByRole('status')).toHaveTextContent(/on account 12346/i)
})

test('the Flattened ✓ confirmation reverts to the idle row after a few seconds', async () => {
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

    await chooseFromMenu('12346', /^flatten$/i)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))
    expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.queryByRole('button', { name: /flattened/i })).not.toBeInTheDocument()
    // Back to idle: no flatten status on any row; Flatten lives in the menu again.
    expect(screen.queryByRole('button', { name: /flatten/i })).not.toBeInTheDocument()
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

  await chooseFromMenu('12346', /^flatten$/i)
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  const retryButton = await screen.findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  await userEvent.click(retryButton)
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The stale failure alert must not contradict the fresh success notice.
  expect(screen.queryByText(/flatten failed on account 12346/i)).not.toBeInTheDocument()
})

test('flatten failure flags the row and names the account in the error', async () => {
  setRole('admin')
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({ detail: 'copier unreachable' }, 502),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^flatten$/i)
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  const retryButton = await within(rowFor('12346')).findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  // The failed button is a retry: clicking it reopens the confirmation.
  await userEvent.click(retryButton)
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
})

// ---------- Role gating ----------

test('viewer (below control) gets read-only rows and a read-only drawer: no menu, no editors, no connect', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // No actions menu on any row.
  expect(screen.queryByRole('button', { name: /^actions for/i })).not.toBeInTheDocument()
  // ...but the read-only values are still shown, in the product's words.
  expect(screen.getByText('Master')).toBeInTheDocument()
  expect(screen.getByText('Follower')).toBeInTheDocument()
  expect(screen.getByText('Second desk')).toBeInTheDocument()

  expect(screen.queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
  // Flatten and Re-grant access are the same destructive/OAuth class as the
  // gated controls above and must be hidden below control too.
  expect(screen.queryByRole('button', { name: /^flatten$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /re-grant access/i })).not.toBeInTheDocument()

  // The drawer shows the settings but offers no editor.
  const drawer = await openDrawer('12345')
  expect(within(drawer).queryByLabelText('Role')).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText(/multiplier/i)).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText('Copying enabled')).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText('Nickname')).not.toBeInTheDocument()
  expect(within(drawer).queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument()
})

test('admin (control) edits in the drawer, acts from the row menu, and has connect and re-grant in the header', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  expect(screen.getByRole('button', { name: /connect ctrader id/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /re-grant access/i })).toBeInTheDocument()

  const drawer = await openDrawer('12345')
  expect(within(drawer).getByLabelText('Role')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Copying enabled')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Nickname')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Cutoff date')).toBeInTheDocument()
  expect(within(drawer).getByRole('button', { name: /save changes/i })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

  const menu = await openMenu('12346')
  expect(within(menu).getByRole('menuitem', { name: /^flatten$/i })).toBeInTheDocument()
  expect(within(menu).getByRole('menuitem', { name: /^disconnect$/i })).toBeInTheDocument()
})

test('each row has one actions menu that opens from the keyboard and lists what applies to its platform', async () => {
  setRole('admin')
  const lapsed = {
    ...mockAccounts[1], ctid_trader_account_id: 3, trader_login: 12347,
    nickname: 'Lapsed desk', connection_status: 'expired',
  }
  const mt5Master = {
    ...mt5Account, ctid_trader_account_id: 1000000000004, nickname: 'Master terminal',
    role: 'master', mt5: { ...mt5Account.mt5!, login: 777, broker: 'ABC Ltd' },
  }
  mockRoutes({
    '/api/orgs/1/accounts': () => jsonResponse([mockAccounts[0], lapsed, mt5Account, mt5Master]),
  })
  renderAccounts()
  await screen.findByText('12345')

  const expectations: Array<[string, string[]]> = [
    ['12345', ['Flatten', 'Disconnect']],
    // A lapsed cTrader grant is the one place Re-grant appears on a row.
    ['Lapsed desk', ['Flatten', 'Re-grant access', 'Disconnect']],
    ['XYZ Ltd', ['Flatten', 'Rotate key', 'Remove']],
    // The master is never offered Remove.
    ['ABC Ltd', ['Flatten', 'Rotate key']],
  ]
  for (const [text, items] of expectations) {
    const trigger = within(rowFor(text)).getByRole('button', { name: /^actions for/i })
    act(() => trigger.focus())
    await userEvent.keyboard('{Enter}')
    const menu = await screen.findByRole('menu')
    expect(menuItemNames(menu)).toEqual(items)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  }
})

test('Re-grant access lives in the page header, disabled until a cTrader grant needs it', async () => {
  setRole('admin')
  mockRoutes()
  const { unmount } = renderAccounts()

  await screen.findByText('12345')
  // Every grant is active: nothing to re-grant (Connect cTrader ID still is).
  expect(screen.getByRole('button', { name: /re-grant access/i })).toBeDisabled()
  expect(within(screen.getByRole('table')).queryByRole('button', { name: /re-grant access/i }))
    .not.toBeInTheDocument()
  unmount()

  const lapsed = { ...mockAccounts[1], connection_status: 'expired' }
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([mockAccounts[0], lapsed]) })
  renderAccounts()

  await screen.findByText('12346')
  expect(within(rowFor('12346')).getByText('Expired')).toBeInTheDocument()
  const regrant = screen.getByRole('button', { name: /re-grant access/i })
  expect(regrant).toBeEnabled()
  await userEvent.click(regrant)
  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
})

test('an empty workspace shows one sentence and a Connect cTrader ID button', async () => {
  setRole('admin')
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([]) })
  renderAccounts()

  expect(await screen.findByText(/no accounts yet/i)).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  // One in the header, one in the empty state.
  const connects = screen.getAllByRole('button', { name: /connect ctrader id/i })
  expect(connects).toHaveLength(2)
  await userEvent.click(connects[1])
  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
})

test('a viewer sees the empty sentence without a Connect button', async () => {
  setRole('viewer')
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([]) })
  renderAccounts()

  expect(await screen.findByText(/no accounts yet/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
})

test('shows each account\'s live equity, and a dash when the engine has no reading', async () => {
  // Operators asked for per-account equity on this screen: the header only
  // ever showed the MASTER's, so there was no way to see at a glance that a
  // follower had drifted far from the others, or been drained by a margin call.
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

// ---------- MT5 rows ----------

test('rows carry a platform badge; an MT5 row shows login and broker instead of a cTID', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  const rows = await screen.findAllByRole('row')
  const mt5Row = rows.find((r) => r.textContent?.includes('XYZ Ltd'))!
  expect(mt5Row).toBeTruthy()
  expect(within(mt5Row).getByText('MT5')).toBeInTheDocument()
  expect(within(mt5Row).getByText('MT5 · login 555 · XYZ Ltd')).toBeInTheDocument()
  expect(within(mt5Row).queryByText(/cTID/)).not.toBeInTheDocument()

  const ctraderRow = rows.find((r) => r.textContent?.includes('12345'))!
  expect(within(ctraderRow).getByText('cTrader')).toBeInTheDocument()
  expect(within(ctraderRow).getByText('cTID 1')).toBeInTheDocument()
})

test('an MT5 row shows connected, offline with last seen, or waiting for the terminal', async () => {
  setRole('admin')
  const offline = {
    ...mt5Account, ctid_trader_account_id: 1000000000002, nickname: 'Offline desk',
    connection_status: 'offline',
    mt5: { ...mt5Account.mt5!, connected: false, last_seen_at: '2026-09-07T09:00:00+00:00' },
  }
  const fresh = {
    ...mt5Account, ctid_trader_account_id: 1000000000003, nickname: 'New desk',
    trader_login: 0, connection_status: 'never',
    mt5: { ...mt5Account.mt5!, login: null, broker: null, connected: false, last_seen_at: null },
  }
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([mt5Account, offline, fresh]) })
  renderAccounts()

  expect(await screen.findByText('Connected')).toBeInTheDocument()
  expect(screen.getByText(/^Offline · last seen /)).toBeInTheDocument()
  expect(screen.getByText('Waiting for the terminal')).toBeInTheDocument()
  // No login yet: the subtitle says so instead of printing "login 0".
  expect(screen.getByText('MT5 · no login yet')).toBeInTheDocument()
})

test('an MT5 row has no Re-grant access or Disconnect: there is no OAuth grant behind it', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  await screen.findAllByRole('row')
  const menu = await openMenu('XYZ Ltd')
  expect(within(menu).queryByRole('menuitem', { name: /re-grant access/i })).not.toBeInTheDocument()
  expect(within(menu).queryByRole('menuitem', { name: /disconnect/i })).not.toBeInTheDocument()
  // The per-account kill switch still applies: Close all covers MT5 too.
  expect(within(menu).getByRole('menuitem', { name: /^flatten$/i })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  // Re-grant is one header action, never a row button.
  expect(screen.getAllByRole('button', { name: /re-grant access/i })).toHaveLength(1)
  expect(within(screen.getByRole('table')).queryByRole('button', { name: /re-grant access/i }))
    .not.toBeInTheDocument()
})

// ---------- Add MT5 account ----------

const created = {
  account_id: 1000000000001,
  key: 'mt5_test_key_abc',
  download_url: '/downloads/MirrorFleet.mq5',
  install: [
    'Download MirrorFleet.mq5',
    'Copy it to MQL5/Experts and compile it in MetaEditor',
    'Allow https://mirrorfleet.com in Tools → Options → Expert Advisors',
    'Attach it to any chart and paste the key into InpKey',
    'Confirm the row here says Connected',
  ],
}

test('Add MT5 account asks for a nickname, POSTs it, then shows the key once with the install steps', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts': () => jsonResponse(created, 201),
  })
  renderAccounts()

  await userEvent.click(await screen.findByRole('button', { name: /add mt5 account/i }))
  const dialog = await screen.findByRole('dialog')
  // A nameless MT5 row would be unidentifiable until its terminal connects.
  expect(within(dialog).getByRole('button', { name: /create account/i })).toBeDisabled()
  expect(dialog).toHaveTextContent(/disabled follower/i)
  await userEvent.type(within(dialog).getByLabelText(/nickname/i), 'VPS desk')
  await userEvent.click(within(dialog).getByRole('button', { name: /create account/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/mt5/accounts' && (init as RequestInit)?.method === 'POST')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ nickname: 'VPS desk' })
  })

  const reveal = await screen.findByRole('dialog', { name: /mt5 account added/i })
  expect(within(reveal).getByText('mt5_test_key_abc')).toBeInTheDocument()
  expect(within(reveal).getByRole('link', { name: /download mirrorfleet\.mq5/i }))
    .toHaveAttribute('href', '/downloads/MirrorFleet.mq5')
  expect(within(reveal).getAllByRole('listitem')).toHaveLength(5)

  // Closing the dialog is the last time the key is on screen.
  await userEvent.click(within(reveal).getByRole('button', { name: /i have copied it/i }))
  expect(screen.queryByText('mt5_test_key_abc')).not.toBeInTheDocument()
})

test('the key dialog copies the key to the clipboard', async () => {
  setRole('admin')
  mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts': () => jsonResponse({ ...created, install: [] }, 201),
  })
  renderAccounts()

  await userEvent.click(await screen.findByRole('button', { name: /add mt5 account/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.type(within(dialog).getByLabelText(/nickname/i), 'VPS desk')
  await userEvent.click(within(dialog).getByRole('button', { name: /create account/i }))
  const reveal = await screen.findByRole('dialog', { name: /mt5 account added/i })

  // Installed AFTER every userEvent call: user-event swaps in its own
  // clipboard stub on first use, and this one must be the one the page hits.
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  fireEvent.click(within(reveal).getByRole('button', { name: /^copy$/i }))

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('mt5_test_key_abc'))
  expect(await within(reveal).findByRole('button', { name: /^copied$/i })).toBeInTheDocument()
})

test('viewer sees no Add MT5 account button', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  expect(screen.queryByRole('button', { name: /add mt5 account/i })).not.toBeInTheDocument()
})

// ---------- Rotate key ----------

test('Rotate key confirms, POSTs the rotation, and shows the new key once', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts/1000000000001/key': () => jsonResponse({ key: 'mt5_rotated_key' }),
  })
  renderAccounts()

  await screen.findByText('MT5 · login 555 · XYZ Ltd')
  await chooseFromMenu('XYZ Ltd', /^rotate key$/i)
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/stops working/i)
  // Nothing sent until confirmed: the running EA goes dark the moment it is.
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/key'))).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /^rotate key$/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/mt5/accounts/1000000000001/key',
      expect.objectContaining({ method: 'POST' })
    )
  })
  const reveal = await screen.findByRole('dialog', { name: /new key for vps desk/i })
  expect(within(reveal).getByText('mt5_rotated_key')).toBeInTheDocument()
  // A rotation replaces only the key: no download link, no install steps.
  expect(within(reveal).queryByRole('link')).not.toBeInTheDocument()
  expect(within(reveal).queryByRole('listitem')).not.toBeInTheDocument()
})

test('below control, an MT5 row shows no Rotate key', async () => {
  setRole('viewer')
  mockMt5Routes()
  renderAccounts()

  await screen.findByText('MT5 · login 555 · XYZ Ltd')
  expect(screen.queryByRole('button', { name: /rotate key/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^actions for/i })).not.toBeInTheDocument()
})

// ---------- MT5 details drawer ----------

const mt5Details = {
  account_id: 1000000000001, trader_login: 555, balance: 9784.04, deposit_currency: 'USD',
  leverage: 500, max_leverage: null, broker_name: 'XYZ Ltd',
  registration_timestamp: null, account_type: 'HEDGED', access_rights: null, swap_free: null,
  is_limited_risk: false, open_positions: [], pending_orders: [],
  nickname: 'VPS desk', role: 'slave', enabled: false, multiplier: 1, status: 'ok',
  last_error: null, is_live: true,
}

const mt5Aliases = {
  aliases: [
    { canonical: 'XAUUSD', broker_name: 'XAUUSD.r', source: 'auto' },
    { canonical: 'EURUSD', broker_name: 'EURUSD.r', source: 'manual' },
  ],
  broker_symbols: ['XAUUSD.r', 'EURUSD.r', 'GBPUSD.r'],
}

async function openMt5Details() {
  const rows = await screen.findAllByRole('row')
  const mt5Row = rows.find((r) => r.textContent?.includes('XYZ Ltd'))!
  await userEvent.click(within(mt5Row).getByRole('button', { name: /details/i }))
}

test("an MT5 account's details show a Terminal section instead of the OAuth grant, and the symbol mapping", async () => {
  setRole('admin')
  mockMt5Routes({
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  const terminal = (await screen.findByRole('heading', { name: 'Terminal' })).closest('section')!
  expect(within(terminal).getByText('XYZ-Live3')).toBeInTheDocument()
  expect(within(terminal).getByText('real')).toBeInTheDocument()
  expect(within(terminal).getByText('1.0.0')).toBeInTheDocument()
  expect(screen.queryByText('OAuth grant')).not.toBeInTheDocument()

  const mapping = screen.getByRole('heading', { name: 'Symbol mapping' }).closest('section')!
  const input = await within(mapping).findByLabelText('Broker symbol for XAUUSD')
  expect((input as HTMLInputElement).value).toBe('XAUUSD.r')
  expect(within(mapping).getByText('XAUUSD')).toBeInTheDocument()
  expect(within(mapping).getByText('auto')).toBeInTheDocument()
  expect(within(mapping).getByText('manual')).toBeInTheDocument()

  // The spec puts Rotate key in the Terminal section: it opens the same
  // confirm dialog as the row menu, stacked above the drawer.
  await userEvent.click(within(terminal).getByRole('button', { name: /rotate key/i }))
  expect(await screen.findByRole('dialog', { name: /rotate the key for vps desk/i })).toBeInTheDocument()
})

test('a viewer opening an MT5 details drawer sees the admin-only notice, not the mapping', async () => {
  setRole('viewer')
  mockMt5Routes({
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  expect(await screen.findByText('Only an admin can see the mapping.')).toBeInTheDocument()
})

test('editing a broker symbol PUTs that one alias on blur and reloads the mapping', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'PUT /accounts/1000000000001/symbol-aliases': () => jsonResponse({ status: 'ok' }),
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  const input = await screen.findByLabelText('Broker symbol for XAUUSD')
  await userEvent.clear(input)
  await userEvent.type(input, 'GOLD.r')
  await userEvent.tab()

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/accounts/1000000000001/symbol-aliases' &&
      (init as RequestInit)?.method === 'PUT')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ aliases: { XAUUSD: 'GOLD.r' } })
  })
  // Re-read after the save so the auto/manual tag is the server's truth.
  await waitFor(() => {
    const reads = fetchMock.mock.calls.filter(([u, init]) =>
      String(u).endsWith('/symbol-aliases') && ((init as RequestInit)?.method ?? 'GET') === 'GET')
    expect(reads.length).toBeGreaterThanOrEqual(2)
  })
})

test('a new canonical → broker mapping can be added from the drawer', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'PUT /accounts/1000000000001/symbol-aliases': () => jsonResponse({ status: 'ok' }),
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  await screen.findByLabelText('Broker symbol for XAUUSD')
  const addButton = screen.getByRole('button', { name: /add mapping/i })
  expect(addButton).toBeDisabled()
  await userEvent.type(screen.getByLabelText('New canonical symbol'), 'us500')
  await userEvent.type(screen.getByLabelText('New broker symbol'), 'US500.cash')
  await userEvent.click(addButton)

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/accounts/1000000000001/symbol-aliases' &&
      (init as RequestInit)?.method === 'PUT')
    expect(call).toBeTruthy()
    // Canonical names are upper-case, as master events carry them.
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ aliases: { US500: 'US500.cash' } })
  })
})

test('Remove appears only on MT5 non-master rows, confirms, then DELETEs', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes()
  renderAccounts()

  const rows = await screen.findAllByRole('row')
  expect(rows.some((r) => r.textContent?.includes('XYZ Ltd'))).toBe(true)

  // cTrader rows never offer Remove (they disconnect their grant instead).
  for (const text of ['12345', '12346']) {
    const menu = await openMenu(text)
    expect(within(menu).queryByRole('menuitem', { name: /^remove$/i })).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
  }

  await chooseFromMenu('XYZ Ltd', /^remove$/i)

  // No DELETE yet: removal is permanent, so it asks first.
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/permanently deletes/i)
  expect(fetchMock.mock.calls.some(([, init]) =>
    (init as RequestInit)?.method === 'DELETE')).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /remove permanently/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/mt5/accounts/1000000000001' &&
      (init as RequestInit)?.method === 'DELETE')
    expect(call).toBeTruthy()
  })
})
