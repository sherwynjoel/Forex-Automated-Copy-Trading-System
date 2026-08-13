import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import Accounts from './Accounts'

let mockWindowOpen: ReturnType<typeof vi.fn>
let mockConfirm: ReturnType<typeof vi.fn>
let focusListeners: Set<(event: Event) => void> = new Set()

beforeEach(() => {
  mockWindowOpen = vi.fn()
  mockConfirm = vi.fn()
  focusListeners.clear()

  Object.defineProperty(window, 'open', {
    value: mockWindowOpen,
    writable: true,
  })
  Object.defineProperty(window, 'confirm', {
    value: mockConfirm,
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
    status: 'active',
    last_error: null,
    connection_status: 'connected',
  },
  {
    ctid_trader_account_id: 2,
    trader_login: 12346,
    is_live: true,
    role: 'slave',
    enabled: true,
    multiplier: 2.0,
    status: 'active',
    last_error: null,
    connection_status: 'connected',
  },
]

test('loads and displays accounts on mount', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
})

test('connect button opens oauth popup', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValue(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByRole('button', { name: /connect ctrader id/i })).toBeInTheDocument()
  })

  const connectButton = screen.getByRole('button', { name: /connect ctrader id/i })
  await userEvent.click(connectButton)

  expect(mockWindowOpen).toHaveBeenCalledWith(
    '/api/oauth/connect',
    'ctrader-oauth',
    'width=520,height=680'
  )
})

test('window-focus refetch: refetches accounts after OAuth popup closes', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // Simulate window focus event
  act(() => {
    focusListeners.forEach((listener) => listener(new Event('focus')))
  })

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    expect(lastCall[0]).toBe('/api/accounts')
  })
})

test('re-grant access button opens oauth popup', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValue(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const reGrantButtons = screen.getAllByRole('button', { name: /re-grant access/i })
  await userEvent.click(reGrantButtons[0])

  expect(mockWindowOpen).toHaveBeenCalledWith(
    '/api/oauth/connect',
    'ctrader-oauth',
    'width=520,height=680'
  )
})

test('role select PATCHes role', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response(null, { status: 204 })
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // Find and change the role select for account 1 (master)
  const roleSelects = screen.getAllByRole('combobox')
  const masterSelect = roleSelects.find((select) => (select as HTMLSelectElement).value === 'master') as HTMLSelectElement

  await userEvent.selectOptions(masterSelect, 'slave')

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
      })
    )
  })
})

test('409 on second master shows inline error', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response('Conflict: a master already exists', { status: 409 })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  // Find the role select for account 2 (slave) - it's the second select
  const roleSelects = screen.getAllByRole('combobox')
  const slaveSelect = roleSelects.find((select) => (select as HTMLSelectElement).value === 'slave') as HTMLSelectElement

  if (slaveSelect) {
    await userEvent.selectOptions(slaveSelect, 'master')

    await waitFor(() => {
      expect(screen.getByText(/a master already exists/i)).toBeInTheDocument()
    })
  }
})

test('multiplier edit PATCHes multiplier', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response(null, { status: 204 })
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  // Find multiplier input for slave account (account 2)
  const multiplierInputs = screen.getAllByDisplayValue('2')
  const multiplierInput = multiplierInputs[0]

  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '3.5')
  await userEvent.tab() // Trigger blur

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

test('disconnect confirms then DELETEs connection', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response(null, { status: 204 })
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify([mockAccounts[0]]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)
  mockConfirm.mockReturnValue(true)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  // Find and click disconnect button for slave account (account 2)
  const disconnectButtons = screen.getAllByRole('button', { name: /disconnect/i })
  await userEvent.click(disconnectButtons[disconnectButtons.length - 1])

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/connections/2',
      expect.objectContaining({
        method: 'DELETE',
      })
    )
  })
})

test('multiplier validation: rejects values <= 0', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  // Find multiplier input for slave account (account 2)
  const multiplierInputs = screen.getAllByDisplayValue('2')
  const multiplierInput = multiplierInputs[0] as HTMLInputElement

  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '0')
  await userEvent.tab() // Trigger blur

  // Should show validation error
  await waitFor(() => {
    expect(screen.getByText(/multiplier must be greater than 0/i)).toBeInTheDocument()
  })
})

test('multiplier 400 error handling shows inline error', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response('Invalid multiplier', { status: 400 })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  // Find multiplier input for slave account (account 2)
  const multiplierInputs = screen.getAllByDisplayValue('2')
  const multiplierInput = multiplierInputs[0]

  await userEvent.clear(multiplierInput)
  await userEvent.type(multiplierInput, '5.0')
  await userEvent.tab() // Trigger blur

  // Should show error message without crashing
  await waitFor(() => {
    expect(screen.getByText(/Failed to update multiplier/i)).toBeInTheDocument()
  })
})

test('enabled toggle PATCHes enabled field (not role)', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    .mockResolvedValueOnce(
      new Response(null, { status: 204 })
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify(mockAccounts), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  vi.stubGlobal('fetch', fetchMock)

  render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // Find and click enabled checkbox for account 1
  const checkboxes = screen.getAllByRole('checkbox')
  await userEvent.click(checkboxes[0])

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    )
  })

  // Verify role was NOT changed
  const patchCalls = fetchMock.mock.calls.filter((call) => call[1]?.method === 'PATCH')
  expect(patchCalls).toHaveLength(1)
  expect(patchCalls[0][1].body).toContain('enabled')
  expect(patchCalls[0][1].body).not.toContain('role')
})
