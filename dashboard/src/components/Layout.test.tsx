import { act, render, screen, waitFor, within, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import { useEffect } from 'react'
import Layout from './Layout'
import PageHeader from './PageHeader'
import Overview from '../pages/Overview'
import type { Role } from '../lib/roles'
import { mt5Account } from '../test/mt5Fixtures'
import { readSettings } from '../lib/settingsBus'

const { useOrgMock, navigateMock } = vi.hoisted(() => ({
  useOrgMock: vi.fn(),
  navigateMock: vi.fn(),
}))

vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

const { fakeSockets } = vi.hoisted(() => ({
  fakeSockets: [] as Array<{ onmessage: ((e: { data: string }) => void) | null }>,
}))
vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    eventsSocket: () => {
      const ws = { onmessage: null, onclose: null, onerror: null, close: () => {} }
      fakeSockets.push(ws as never)
      return ws as unknown as WebSocket
    },
  }
})

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function makeOrgValue(role: Role, orgsOverride?: Array<{ id: number; name: string; role: Role }>) {
  const orgs = orgsOverride ?? [{ id: 1, name: 'Acme', role }]
  return {
    orgId: 1,
    role,
    org: { id: 1, name: 'Acme', role },
    me: {
      user: { id: 1, email: 'ada@example.com', display_name: 'Ada' },
      orgs,
    },
    refreshMe: vi.fn(),
  }
}

const settings = { copying_enabled: true, dry_run: false, shards: 1 }
const apiState = {
  accounts: { '1': { balance: 10000, open_pnl: 25.5, equity: 10025.5, positions: [] } },
  master_positions: [],
  pending_orders: [],
  drift: [],
}
const accounts = [
  {
    ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
    enabled: true, multiplier: 1.0, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null,
  },
]

function mockRoutes(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const respond = (payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/notifications/unread-count')) return respond(overrides['unread'] ?? { count: 0 })
    if (url.includes('/requests/summary')) {
      return respond(overrides['requests']
        ?? { deposits: 0, withdrawals: 0, transfers: 0, payout_destinations: 0, total: 0 })
    }
    if (url.includes('category=reminder')) return respond(overrides['reminderEvents'] ?? [])
    if (url.includes('/events')) return respond(overrides['events'] ?? [])
    if (url.includes('/webhook')) {
      return respond(overrides['webhook'] ?? { configured: true, enabled: true })
    }
    if (url.includes('/settings')) return respond(overrides['settings'] ?? settings)
    if (url.includes('/state')) return respond(overrides['state'] ?? apiState)
    if (url.includes('/accounts')) return respond(overrides['accounts'] ?? accounts)
    if (url.includes('/control/close-all')) {
      return respond(
        overrides['closeAll'] ?? { status: 'flattened', paused: false, accounts: [] })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

type MqListener = (e: { matches: boolean }) => void

/** matchMedia stub for jsdom (same shape as useTheme.test.ts): captures the
 *  `(min-width: 1024px)` change listener Layout registers so a test can
 *  fire it without a real viewport resize. */
function stubMatchMedia(): { fire: (matches: boolean) => void } {
  const listeners: MqListener[] = []
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener: (_: string, cb: MqListener) => listeners.push(cb),
    removeEventListener: (_: string, cb: MqListener) => {
      const i = listeners.indexOf(cb)
      if (i >= 0) listeners.splice(i, 1)
    },
  }))
  return { fire: (m: boolean) => listeners.forEach((cb) => cb({ matches: m })) }
}

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>page body</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

test('desk strip shows copying state and master numbers', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  await waitFor(() => {
    expect(screen.getByText(/copying live/i)).toBeInTheDocument()
  })
  // Equity and open P&L from the master's state block, mono-formatted
  expect(await screen.findByText('10,025.50')).toBeInTheDocument()
  expect(screen.getByText('+25.50')).toBeInTheDocument()
})

test('desk strip shows paused state when copying is disabled', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await waitFor(() => {
    expect(screen.getByText(/copying paused/i)).toBeInTheDocument()
  })
})

test('desk strip shows Automation on next to the copying state, linking to the page', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ webhook: { configured: true, enabled: true } })
  renderLayout()

  const pill = await screen.findByText(/automation on/i)
  expect(pill.closest('a')).toHaveAttribute('href', '/org/1/automation')
})

test('desk strip shows Automation off when the switch is off', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ webhook: { configured: true, enabled: false } })
  renderLayout()

  expect(await screen.findByText(/automation off/i)).toBeInTheDocument()
})

test('viewers, who cannot open Automation, get no automation pill and no request for it', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  const fetchMock = mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/automation o/i)).toBeNull()
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/webhook'))).toBe(false)
})

test('close-all requires the CLOSE ALL phrase before it will confirm', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  const openButton = await screen.findByRole('button', { name: /close all positions/i })
  await userEvent.click(openButton)

  // Dialog open with its consequence copy; nothing sent until the phrase
  // is typed and the confirm button unlocks.
  const confirmButton = screen.getByRole('button', { name: /^close every position$/i })
  expect(confirmButton).toBeDisabled()
  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/control/close-all'))
  ).toBe(false)

  await userEvent.type(screen.getByLabelText(/type close all to continue/i), 'CLOSE ALL')
  expect(confirmButton).toBeEnabled()
  await userEvent.click(confirmButton)

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/control/close-all'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toBe('/api/orgs/1/control/close-all')
    expect((call![1] as RequestInit).method).toBe('POST')
    expect((call![1] as RequestInit).body).toBe('{}')
  })

  // The button only closes contracts: the outcome notice must say copying
  // survived (the copier restores it after its transient flatten pause).
  expect(
    await screen.findByText(/Copying is still running/)
  ).toBeInTheDocument()
})

test('the close-all confirm button stays disabled until CLOSE ALL is typed exactly', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /close all positions/i }))
  const confirmButton = screen.getByRole('button', { name: /^close every position$/i })
  const phrase = screen.getByLabelText(/type close all to continue/i)

  expect(confirmButton).toBeDisabled()
  await userEvent.type(phrase, 'close all')
  expect(confirmButton).toBeDisabled()
  await userEvent.clear(phrase)
  await userEvent.type(phrase, 'CLOSE ALL')
  expect(confirmButton).toBeEnabled()
})

test('close-all on a stopped org says copying stays stopped', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({
    closeAll: { status: 'flattened', paused: true, accounts: [] },
    settings: { copying_enabled: false, dry_run: false },
  })
  renderLayout()

  await userEvent.click(
    await screen.findByRole('button', { name: /close all positions/i }))
  // The dialog must not promise survival it cannot deliver on a paused org.
  expect(screen.getByText(/stays stopped/i)).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText(/type close all to continue/i), 'CLOSE ALL')
  await userEvent.click(
    screen.getByRole('button', { name: /^close every position$/i }))
  expect(await screen.findByText(/Copying is stopped\./)).toBeInTheDocument()
})

test('cancelling the close-all dialog sends nothing', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(
    await screen.findByRole('button', { name: /close all positions/i }))
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/control/close-all'))
  ).toBe(false)
})

test('the close-all dialog only counts accounts the copier would actually flatten', async () => {
  // The copier flattens `enabled && status !== 'paused'` -- a per-slave
  // Pause leaves `enabled` true but sets status 'paused'. Counting `enabled`
  // alone overstated the dialog's blast radius by every paused slave.
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({
    accounts: [
      accounts[0], // master: enabled, status 'ok' -> counted
      { ...accounts[0], ctid_trader_account_id: 2, trader_login: 22222,
        role: 'slave', enabled: true, status: 'paused' }, // paused -> not counted
      { ...accounts[0], ctid_trader_account_id: 3, trader_login: 33333,
        role: 'slave', enabled: false, status: 'ok' }, // disabled -> not counted
    ],
  })
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /close all positions/i }))
  expect(await screen.findByText(/1 enabled account/i)).toBeInTheDocument()
})

test('recent margin-call risk event raises a banner', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({
    events: [{
      id: 9, ts: new Date().toISOString(), account_id: 12345,
      category: 'risk', severity: 'error', latency_ms: null,
      payload: { action: 'margin_call', margin_call_type: 'MARGIN_LEVEL_THRESHOLD_1',
                 margin_level_threshold: 50 },
    }],
  })
  renderLayout()

  expect(await screen.findByText(/margin call/i)).toBeInTheDocument()
  expect(screen.getByText(/12345/)).toBeInTheDocument()
})

test('the risk-event poll behind the banner is org-scoped', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/events'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toBe('/api/orgs/1/events?category=risk&limit=5')
  })
})

function isoDaysFromNow(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10)
}

function reminderEvent(cutoffDate: string, overrides: Record<string, unknown> = {}) {
  return {
    id: 7, ts: new Date().toISOString(), account_id: 12345,
    category: 'reminder', severity: 'warning', latency_ms: null,
    payload: { action: 'cutoff_approaching', cutoff_date: cutoffDate,
               days_left: 2, nickname: 'FTMO demo', trader_login: 555 },
    ...overrides,
  }
}

test('an upcoming cutoff reminder raises a banner until the date passes', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const cutoff = isoDaysFromNow(2)
  mockRoutes({ reminderEvents: [reminderEvent(cutoff)] })
  renderLayout()

  expect(await screen.findByText(/account cutoff/i)).toBeInTheDocument()
  expect(screen.getByText(/FTMO demo/)).toBeInTheDocument()
  expect(screen.getByText(new RegExp(cutoff))).toBeInTheDocument()
})

test('the reminder poll behind the cutoff banner is org-scoped', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('category=reminder'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toBe('/api/orgs/1/events?category=reminder&limit=5')
  })
})

test('a reminder whose cutoff has already passed raises no banner', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ reminderEvents: [reminderEvent(isoDaysFromNow(-3))] })
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/account cutoff/i)).not.toBeInTheDocument()
})

test('dismissing the cutoff banner hides it', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ reminderEvents: [reminderEvent(isoDaysFromNow(2))] })
  renderLayout()

  await screen.findByText(/account cutoff/i)
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/account cutoff/i)).not.toBeInTheDocument()
})

test('no banner without recent risk events', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/margin call/i)).not.toBeInTheDocument()
})

test('hides the close-all kill switch below admin', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByRole('button', { name: /close all positions/i })).not.toBeInTheDocument()
})

test('shows the kill switch for admin', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  expect(await screen.findByRole('button', { name: /close all positions/i })).toBeInTheDocument()
})

test('on phones the strip is one row: short labels, full accessible names, P&L and Automation from md up', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  const closeAll = await screen.findByRole('button', { name: 'Close all positions' })
  expect(within(closeAll).getByText('Close all')).toHaveClass('md:hidden')
  expect(within(closeAll).getByText('Close all positions')).toHaveClass('hidden', 'md:inline')
  const stop = await screen.findByRole('button', { name: 'Stop copying' })
  expect(within(stop).getByText('Stop')).toHaveClass('md:hidden')
  expect(screen.getByText('Live')).toHaveClass('md:hidden')
  // Open P&L and the Automation pill are desktop-only; equity keeps a
  // screen-reader label on phones.
  expect(screen.getByText('Open P&L').parentElement).toHaveClass('hidden', 'md:flex')
  expect((await screen.findByText('Automation on')).closest('a')).toHaveClass('hidden', 'md:flex')
  expect(screen.getByText('Master equity')).toHaveClass('sr-only', 'md:not-sr-only')
})

test('hides the Trade nav item for viewers', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying/i)
  expect(screen.queryByRole('link', { name: 'Trade' })).not.toBeInTheDocument()
})

test('org switcher lists my orgs and navigates on change', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin', [
    { id: 1, name: 'Acme', role: 'admin' },
    { id: 2, name: 'Widgets', role: 'admin' },
  ]))
  mockRoutes()
  renderLayout()

  const select = await screen.findByLabelText('Organization')
  expect(screen.getByRole('option', { name: 'Acme' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Widgets' })).toBeInTheDocument()

  await userEvent.selectOptions(select, '2')

  expect(navigateMock).toHaveBeenCalledWith('/org/2')
})

test('the Members nav link is always present, regardless of role', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  expect(await screen.findByRole('link', { name: 'Members' })).toBeInTheDocument()
})

test('the Performance nav link points at the org-scoped route', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  const link = await screen.findByRole('link', { name: 'Performance' })
  expect(link).toHaveAttribute('href', '/org/1/performance')
})

test('mobile menu button opens the Menu drawer and a nav tap closes it', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  // Drawer is closed by default
  expect(screen.queryByRole('dialog', { name: /menu/i })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /open menu/i }))
  const drawer = await screen.findByRole('dialog', { name: /menu/i })
  expect(drawer).toBeInTheDocument()

  // Tapping a nav destination closes the drawer
  const { within } = await import('@testing-library/react')
  await userEvent.click(within(drawer).getByRole('link', { name: /accounts/i }))
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /menu/i })).not.toBeInTheDocument()
  })
})

test('Escape closes the mobile Menu drawer and returns focus to the hamburger', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  const menuButton = screen.getByRole('button', { name: /open menu/i })
  await userEvent.click(menuButton)
  await screen.findByRole('dialog', { name: /menu/i })
  await userEvent.keyboard('{Escape}')
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /menu/i })).not.toBeInTheDocument()
  })
  expect(menuButton).toHaveFocus()
})

test('the phone Menu drawer closes itself when the viewport crosses into desktop width', async () => {
  // The drawer is display:none past `lg`, but its focus trap has no way to
  // know that on its own -- left open, Escape and Tab keep fighting over a
  // panel nobody can see.
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  const mq = stubMatchMedia()
  renderLayout()

  await userEvent.click(screen.getByRole('button', { name: /open menu/i }))
  await screen.findByRole('dialog', { name: /menu/i })

  act(() => {
    mq.fire(true)
  })

  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /menu/i })).not.toBeInTheDocument()
  })
})

test('the sidebar theme toggle names what it will do and flips after a click', async () => {
  renderLayout()
  const toggle = await screen.findAllByRole('button', { name: /switch to dim theme/i })
  await userEvent.click(toggle[0])
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(await screen.findAllByRole('button', { name: /switch to light theme/i })).not.toHaveLength(0)
})

afterEach(() => {
  delete document.documentElement.dataset.theme
  localStorage.clear()
})

test('the desk strip shows each open contract with its live price', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({
    state: {
      accounts: {
        '1': {
          balance: 10000, open_pnl: -4.23, equity: 9995.77,
          positions: [
            { position_id: 42, symbol_id: 1, symbol: 'BTCUSD', side: 'BUY',
              volume: 100, entry_price: 78140.89, pnl_quote: -4.23,
              current_price: 77717.95 },
          ],
        },
      },
      master_positions: [], pending_orders: [], drift: [],
    },
  })
  const socketsBefore = fakeSockets.length
  renderLayout()

  expect(await screen.findByText('BTCUSD')).toBeInTheDocument()
  expect(screen.getByText('77717.95')).toBeInTheDocument()

  // A quotes frame moves the strip price live, no refetch involved. The
  // frame goes to every socket this render opened (the strip's and the
  // Requests badge's); only the strip acts on quotes.
  const frame = JSON.stringify({
    category: 'quotes',
    payload: {
      quotes: {},
      accounts: {
        '1': {
          equity: 9999.9, open_pnl: 0.1,
          positions: [{ position_id: 42, symbol: 'BTCUSD',
                        current_price: 77801.5, pnl_quote: 0.1 }],
        },
      },
    },
  })
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) ws.onmessage?.({ data: frame })
  })

  expect(await screen.findByText('77801.5')).toBeInTheDocument()
  expect(screen.getByText('9,999.90')).toBeInTheDocument()
})

test('the sidebar caption names the platforms the org has connected', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ accounts: [...accounts, mt5Account] })
  renderLayout()

  expect(await screen.findByText('cTrader · MT5')).toBeInTheDocument()
  expect(screen.queryByText('FP Markets · cTrader')).not.toBeInTheDocument()
})

test('a single-platform org gets a single-platform caption', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  const view = renderLayout()
  expect(await screen.findByText('cTrader')).toBeInTheDocument()
  view.unmount()

  mockRoutes({ accounts: [mt5Account] })
  renderLayout()
  expect(await screen.findByText('MT5')).toBeInTheDocument()
})

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/org/:orgId" element={<Layout />}>
          <Route index element={<div>desk home</div>} />
          <Route path="invest" element={<div>investor home</div>} />
          <Route path="invest/deposit" element={<div>investor deposit</div>} />
          <Route path="investors" element={<div>admin investors</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

test('an investor sees the grouped portal nav, no desk strip and no requests poll', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  const fetchMock = mockRoutes()
  renderShell('/org/1/invest')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  for (const label of ['Dashboard', 'Wallet', 'Deposit', 'Withdraw', 'Transfer', 'Transactions',
                       'Payout accounts', 'Trading account', 'Profile & verification', 'Open account',
                       'Security', 'History']) {
    expect(screen.getAllByRole('link', { name: label }).length).toBeGreaterThan(0)
  }
  expect(screen.getByText('Money')).toBeInTheDocument()
  expect(screen.getByText('Account')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Accounts' })).not.toBeInTheDocument()
  expect(screen.queryByText(/Close all positions/)).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/state'))).toBe(false)
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/requests/summary'))).toBe(false)
  // The phone tab bar has More for investors too.
  expect(screen.getByRole('button', { name: 'More' })).toBeInTheDocument()
})

test('an investor opening a desk path is sent to the portal', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  expect(screen.queryByText('desk home')).not.toBeInTheDocument()
})

test('admins get an Investors nav item and viewers do not', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: 'Investors' }).length).toBeGreaterThan(0)
  cleanup()
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Investors' })).not.toBeInTheDocument()
})

test('an investor opening the admin Investors path is sent to the portal', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  mockRoutes()
  renderShell('/org/1/investors')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  expect(screen.queryByText('admin investors')).not.toBeInTheDocument()
})

test('admins get a Requests nav link that carries the open count from requests/summary', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({
    requests: { deposits: 2, withdrawals: 1, transfers: 0, payout_destinations: 0, total: 3 },
  })
  renderShell('/org/1')
  const links = await screen.findAllByRole('link', { name: 'Requests, 3 open' })
  expect(links[0]).toHaveAttribute('href', '/org/1/requests')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/orgs/1/requests/summary')).toBe(true)
})

test('viewers get no Requests link and the summary is never asked for', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  const fetchMock = mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /^Requests/ })).toBeNull()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/requests/summary'))).toBe(false)
})

test('a control event refreshes the Requests count without waiting for the poll', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  let total = 1
  const fetchMock = mockRoutes()
  const base = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    if (String(input).includes('/requests/summary')) {
      return new Response(JSON.stringify({ deposits: total, withdrawals: 0, transfers: 0, payout_destinations: 0, total }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return base(input)
  })
  const socketsBefore = fakeSockets.length
  renderShell('/org/1')
  await screen.findAllByRole('link', { name: 'Requests, 1 open' })

  total = 4
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) {
      ws.onmessage?.({ data: JSON.stringify({ category: 'control', payload: { action: 'investor_deposit_noticed' } }) })
    }
  })
  expect((await screen.findAllByRole('link', { name: 'Requests, 4 open' }))[0]).toHaveAttribute('href', '/org/1/requests')
})

function renderTwoPages(path: string, onSecondMount?: () => void) {
  function Second() {
    useEffect(() => { onSecondMount?.() }, [])
    return <PageHeader title="Second" />
  }
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/org/:orgId" element={<Layout />}>
          <Route index element={<PageHeader title="First" />} />
          <Route path="positions" element={<Second />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

test('after a nav click the new page heading takes focus; on first load nothing is focused', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderTwoPages('/org/1')

  const first = await screen.findByRole('heading', { level: 1, name: 'First' })
  expect(first).not.toHaveFocus()
  expect(document.activeElement).toBe(document.body)

  const rail = within(screen.getByRole('navigation', { name: 'Main' }))
  await userEvent.click(rail.getByRole('link', { name: 'Positions' }))
  await waitFor(() => {
    expect(screen.getByRole('heading', { level: 1, name: 'Second' })).toHaveFocus()
  })
})

test('a navigation mounts the entering page once, not twice', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  const mounts = vi.fn()
  renderTwoPages('/org/1', mounts)

  await screen.findByRole('heading', { level: 1, name: 'First' })
  const rail = within(screen.getByRole('navigation', { name: 'Main' }))
  await userEvent.click(rail.getByRole('link', { name: 'Positions' }))
  await screen.findByRole('heading', { level: 1, name: 'Second' })
  await waitFor(() => {
    expect(screen.queryByRole('heading', { level: 1, name: 'First' })).toBeNull()
  })
  expect(mounts).toHaveBeenCalledTimes(1)
})

test("switching org never shows, or lets a late answer restore, the previous org's copying state", async () => {
  let current = makeOrgValue('admin')
  useOrgMock.mockImplementation(() => current)
  const respond = (payload: unknown) =>
    new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  let releaseOrg1: (r: Response) => void = () => {}
  let releaseOrg2: (r: Response) => void = () => {}
  const heldOrg1 = new Promise<Response>((r) => { releaseOrg1 = r })
  const heldOrg2 = new Promise<Response>((r) => { releaseOrg2 = r })
  let org1SettingsCalls = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/orgs/1/settings')) {
      org1SettingsCalls += 1
      return org1SettingsCalls === 1 ? respond(settings) : heldOrg1
    }
    if (url.includes('/orgs/2/settings')) return heldOrg2
    if (url.includes('/events')) return respond([])
    if (url.includes('/webhook')) return respond({ configured: true, enabled: true })
    if (url.includes('/state')) return respond(apiState)
    if (url.includes('/accounts')) return respond(accounts)
    return respond({})
  }))
  // A fresh element each time, so the rerender reaches the strip (the org
  // comes from the mocked useOrg, the way an /org/:id change would).
  const tree = () => (
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>page body</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
  const socketsBefore = fakeSockets.length
  const { rerender } = render(tree())
  expect(await screen.findByRole('button', { name: 'Stop copying' })).toBeInTheDocument()

  // A live event starts a second org-1 refresh whose settings answer is held
  // (sent to every socket this render opened: the strip's and the badge's).
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) {
      ws.onmessage?.({ data: JSON.stringify({ category: 'control' }) })
    }
  })
  await waitFor(() => expect(org1SettingsCalls).toBe(2))

  // Switch to org 2: org 1's state is gone at once, nothing to act on yet.
  current = { ...makeOrgValue('admin'), orgId: 2, org: { id: 2, name: 'Beta', role: 'admin' } }
  rerender(tree())
  expect(screen.queryByRole('button', { name: 'Stop copying' })).toBeNull()
  expect(screen.queryByText(/copying live/i)).toBeNull()
  expect(screen.getByText('Connecting…')).toBeInTheDocument()
  expect(readSettings()).toBeNull()

  // Org 2 answers: paused.
  await act(async () => { releaseOrg2(respond({ copying_enabled: false, dry_run: false, shards: 1 })) })
  expect(await screen.findByRole('button', { name: 'Resume copying' })).toBeInTheDocument()

  // Org 1's late answer lands after org 2's and must be ignored.
  await act(async () => { releaseOrg1(respond(settings)) })
  await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
  expect(screen.getByRole('button', { name: 'Resume copying' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Stop copying' })).toBeNull()
  expect(screen.getByText(/copying paused/i)).toBeInTheDocument()
  expect(readSettings()?.copying_enabled).toBe(false)
})

// ---------- the kill switch (moved here from Overview in Task 6) ----------

function settingsPuts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter((call) =>
    String(call[0]).includes('/api/orgs/1/settings') &&
    (call[1] as RequestInit | undefined)?.method === 'PUT')
}

test('the strip kill switch confirms, then PUTs copying_enabled false', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: 'Stop copying' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/stop copying\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: 'Stop copying' }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string).copying_enabled).toBe(false)
  })
})

test('the strip kill switch does not PUT if the dialog is cancelled', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: 'Stop copying' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip kill switch resume: confirming PUTs copying_enabled true', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: 'Resume copying' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/resume copying\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: 'Resume copying' }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string).copying_enabled).toBe(true)
  })
})

test('the strip kill switch resume: cancelling the dialog sends nothing', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: 'Resume copying' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip dry-run toggle PUTs dry_run: true when enabling', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes() // dry_run: false
  renderLayout()

  const toggle = await screen.findByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run on/i)
  await userEvent.click(toggle)

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    // The real value must be in the body -- the dashboard half of the seam
    // where the api once forwarded a hardcoded empty body.
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ dry_run: true })
  })
})

test('enabling dry-run from the strip needs no confirmation (it is the safe direction)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByTestId('dry-run-toggle'))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() => expect(settingsPuts(fetchMock).length).toBeGreaterThan(0))
})

test('disabling dry-run from the strip confirms first, then PUTs dry_run: false', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  const toggle = await screen.findByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run off/i)
  await userEvent.click(toggle)

  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/turn dry-run off\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: /^turn dry-run off$/i }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ dry_run: false })
  })
})

test('disabling dry-run from the strip does not PUT if the dialog is cancelled', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByTestId('dry-run-toggle'))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip Dry run badge reflects the toggled state without a reload', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes() // dry_run: false -> no badge initially
  renderLayout()

  const toggle = await screen.findByTestId('dry-run-toggle')
  expect(screen.queryAllByText('Dry run').filter((el) => !toggle.contains(el))).toHaveLength(0)

  await userEvent.click(screen.getByTestId('dry-run-toggle'))

  // One dry-run marker for an admin: the kill switch's badge, not the strip
  // chip too (both read "Dry run", so exactly one must be on screen). The
  // toggle's own phone label also reads "Dry run"; it is the control, not a
  // second marker, and the badge beside it is hidden below md.
  await waitFor(() => {
    expect(screen.queryAllByText('Dry run').filter((el) => !toggle.contains(el))).toHaveLength(1)
  })
})

test('the strip kill switch is hidden for a viewer (below control)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

test('the strip kill switch is visible for an admin (control)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  expect(await screen.findByRole('button', { name: 'Stop copying' })).toBeInTheDocument()
  expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument()
})

test('a viewer, who has no kill switch, still sees the strip dry-run chip', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  expect(await screen.findByText('Dry run')).toBeInTheDocument()
})

test('stopping copying from the strip updates Overview without a reload', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )

  await waitFor(() =>
    expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — copying live'))

  await userEvent.click(await screen.findByRole('button', { name: 'Stop copying' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Stop copying' }))

  await waitFor(() =>
    expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — copying paused'))
})

/** Fails every settings PUT with a 500; everything else keeps its route. */
function failSettingsPuts(fetchMock: ReturnType<typeof mockRoutes>) {
  const base = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes('/settings') && init?.method === 'PUT') {
      return new Response(JSON.stringify({ detail: 'copier down' }), {
        status: 500, headers: { 'Content-Type': 'application/json' },
      })
    }
    return base(input)
  })
}

test('a failed stop says, in sentence case, that copying is still running', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  failSettingsPuts(mockRoutes())
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: 'Stop copying' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Stop copying' }))

  expect(await screen.findByText(/^Copying is still running — the change failed: /)).toHaveAttribute('role', 'alert')
})

test('a failed dry-run switch says, in sentence case, that dry-run is still off', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  failSettingsPuts(mockRoutes())
  renderLayout()

  // Turning dry-run ON is never gated behind a dialog.
  await userEvent.click(await screen.findByTestId('dry-run-toggle'))

  expect(await screen.findByText(/^Dry-run is still off — the change failed: /)).toHaveAttribute('role', 'alert')
})

test('the theme toggle saves the choice to the account; the desk rail links to Settings', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderShell('/org/1')
  const [toggle] = await screen.findAllByRole('button', { name: /switch to dim theme/i })
  await userEvent.click(toggle)
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u) === '/api/me/settings'
    && (i as RequestInit | undefined)?.method === 'PUT'
    && JSON.parse((i as RequestInit).body as string).theme === 'dim')).toBe(true))
  expect(screen.getAllByRole('link', { name: 'Settings' })[0]).toHaveAttribute('href', '/org/1/settings')
})

test('a manual theme toggle before the account GET answers wins; the late answer is dropped', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  let releaseSettingsGet: ((r: Response) => void) | null = null
  const respond = (payload: unknown) => new Response(JSON.stringify(payload), {
    status: 200, headers: { 'Content-Type': 'application/json' } })
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    // The toggle's own PUT (saveThemePref) hits this same path with a
    // different method -- it must resolve on its own, not share the mount
    // GET's held promise.
    if (url === '/api/me/settings' && method === 'GET') {
      return new Promise<Response>((resolve) => { releaseSettingsGet = resolve })
    }
    if (url === '/api/me/settings' && method === 'PUT') {
      return respond({ ...JSON.parse((init!.body as string)), updated_at: '2026-10-05T10:05:00Z' })
    }
    if (url.includes('/notifications/unread-count')) return respond({ count: 0 })
    if (url.includes('/requests/summary')) {
      return respond({ deposits: 0, withdrawals: 0, transfers: 0, payout_destinations: 0, total: 0 })
    }
    if (url.includes('/events')) return respond([])
    if (url.includes('/webhook')) return respond({ configured: true, enabled: true })
    if (url.includes('/settings')) return respond(settings)
    if (url.includes('/state')) return respond(apiState)
    if (url.includes('/accounts')) return respond(accounts)
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderLayout()

  const [toggle] = await screen.findAllByRole('button', { name: /switch to dim theme/i })
  await userEvent.click(toggle)
  expect(document.documentElement.dataset.theme).toBe('dark')

  // The mount GET, held open until now, answers late with 'light' -- the
  // user's own just-made choice must still stand. The chain from fetch to
  // syncThemeFromServer's `choose` call crosses several microtask ticks
  // (finish() awaits response.json() too), so give it room to settle.
  await act(async () => {
    releaseSettingsGet!(respond({ theme: 'light', updated_at: '2026-10-05T10:00:00Z' }))
    await new Promise((r) => setTimeout(r, 20))
  })
  expect(document.documentElement.dataset.theme).toBe('dark')
})

test('the bell carries the unread count and links to the desk or portal page', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({ unread: { count: 4 } })
  const view = renderShell('/org/1')
  const [deskBell] = await screen.findAllByRole('button', { name: 'Notifications, 4 unread' })
  await userEvent.click(deskBell)
  expect(await screen.findByRole('link', { name: 'See all notifications' }))
    .toHaveAttribute('href', '/org/1/notifications')
  view.unmount()

  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  mockRoutes()
  renderShell('/org/1/invest')
  const [portalBell] = await screen.findAllByRole('button', { name: 'Notifications' })
  await userEvent.click(portalBell)
  expect(await screen.findByRole('link', { name: 'See all notifications' }))
    .toHaveAttribute('href', '/org/1/invest/notifications')
})
