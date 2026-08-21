import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Layout from './Layout'
import type { Role } from '../lib/roles'

const { useOrgMock, navigateMock } = vi.hoisted(() => ({
  useOrgMock: vi.fn(),
  navigateMock: vi.fn(),
}))

vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

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
    if (url.includes('category=reminder')) return respond(overrides['reminderEvents'] ?? [])
    if (url.includes('/events')) return respond(overrides['events'] ?? [])
    if (url.includes('/settings')) return respond(overrides['settings'] ?? settings)
    if (url.includes('/state')) return respond(overrides['state'] ?? apiState)
    if (url.includes('/accounts')) return respond(overrides['accounts'] ?? accounts)
    if (url.includes('/control/close-all')) {
      return respond({ status: 'flattened', paused: true, accounts: [] })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
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
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
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
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await waitFor(() => {
    expect(screen.getByText(/copying paused/i)).toBeInTheDocument()
  })
})

test('close-all confirms with a single click — no typed phrase required', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  const fetchMock = mockRoutes()
  renderLayout()

  const openButton = await screen.findByRole('button', { name: /close all positions/i })
  await userEvent.click(openButton)

  // Dialog open with its consequence copy; nothing sent yet, no input field.
  const confirmButton = screen.getByRole('button', { name: /^close every position$/i })
  expect(confirmButton).toBeEnabled()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/control/close-all'))
  ).toBe(false)

  await userEvent.click(confirmButton)

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/control/close-all'))
    expect(call).toBeTruthy()
    expect(String(call![0])).toBe('/api/orgs/1/control/close-all')
    expect((call![1] as RequestInit).method).toBe('POST')
    expect((call![1] as RequestInit).body).toBe('{}')
  })
})

test('cancelling the close-all dialog sends nothing', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(
    await screen.findByRole('button', { name: /close all positions/i }))
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/control/close-all'))
  ).toBe(false)
})

test('recent margin-call risk event raises a banner', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
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
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
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
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  const cutoff = isoDaysFromNow(2)
  mockRoutes({ reminderEvents: [reminderEvent(cutoff)] })
  renderLayout()

  expect(await screen.findByText(/account cutoff/i)).toBeInTheDocument()
  expect(screen.getByText(/FTMO demo/)).toBeInTheDocument()
  expect(screen.getByText(new RegExp(cutoff))).toBeInTheDocument()
})

test('the reminder poll behind the cutoff banner is org-scoped', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
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
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes({ reminderEvents: [reminderEvent(isoDaysFromNow(-3))] })
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/account cutoff/i)).not.toBeInTheDocument()
})

test('dismissing the cutoff banner hides it', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes({ reminderEvents: [reminderEvent(isoDaysFromNow(2))] })
  renderLayout()

  await screen.findByText(/account cutoff/i)
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/account cutoff/i)).not.toBeInTheDocument()
})

test('no banner without recent risk events', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/margin call/i)).not.toBeInTheDocument()
})

test('hides the close-all kill switch below admin', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('trader'))
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

test('hides the Trade nav item for viewers', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying/i)
  expect(screen.queryByRole('link', { name: 'Trade' })).not.toBeInTheDocument()
})

test('org switcher lists my orgs and navigates on change', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner', [
    { id: 1, name: 'Acme', role: 'owner' },
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

test('mobile menu button opens the navigation drawer and a nav tap closes it', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  renderLayout()

  // Drawer is closed by default
  expect(screen.queryByRole('dialog', { name: /navigation/i })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /open menu/i }))
  const drawer = await screen.findByRole('dialog', { name: /navigation/i })
  expect(drawer).toBeInTheDocument()

  // Tapping a nav destination closes the drawer
  const { within } = await import('@testing-library/react')
  await userEvent.click(within(drawer).getByRole('link', { name: /accounts/i }))
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /navigation/i })).not.toBeInTheDocument()
  })
})

test('Escape closes the mobile navigation drawer', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  renderLayout()

  await userEvent.click(screen.getByRole('button', { name: /open menu/i }))
  await screen.findByRole('dialog', { name: /navigation/i })
  await userEvent.keyboard('{Escape}')
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /navigation/i })).not.toBeInTheDocument()
  })
})
