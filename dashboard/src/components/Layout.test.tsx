import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Layout from './Layout'

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

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
    if (url.includes('/api/events')) return respond(overrides['events'] ?? [])
    if (url.includes('/api/settings')) return respond(overrides['settings'] ?? settings)
    if (url.includes('/api/state')) return respond(overrides['state'] ?? apiState)
    if (url.includes('/api/accounts')) return respond(overrides['accounts'] ?? accounts)
    if (url.includes('/api/control/close-all')) {
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
  mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await waitFor(() => {
    expect(screen.getByText(/copying paused/i)).toBeInTheDocument()
  })
})

test('close-all requires typing CLOSE ALL before the request is sent', async () => {
  const fetchMock = mockRoutes()
  renderLayout()

  const openButton = await screen.findByRole('button', { name: /close all positions/i })
  await userEvent.click(openButton)

  // Dialog open; the confirm button is disabled until the phrase matches.
  const confirmButton = screen.getByRole('button', { name: /^close every position$/i })
  expect(confirmButton).toBeDisabled()
  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/api/control/close-all'))
  ).toBe(false)

  await userEvent.type(screen.getByRole('textbox'), 'CLOSE ALL')
  expect(confirmButton).toBeEnabled()
  await userEvent.click(confirmButton)

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('/api/control/close-all'))
    expect(call).toBeTruthy()
    expect((call![1] as RequestInit).method).toBe('POST')
    expect((call![1] as RequestInit).body).toBe('{}')
  })
})

test('cancelling the close-all dialog sends nothing', async () => {
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(
    await screen.findByRole('button', { name: /close all positions/i }))
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

  expect(
    fetchMock.mock.calls.some(([u]) => String(u).includes('/api/control/close-all'))
  ).toBe(false)
})


test('recent margin-call risk event raises a banner', async () => {
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

test('no banner without recent risk events', async () => {
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByText(/margin call/i)).not.toBeInTheDocument()
})