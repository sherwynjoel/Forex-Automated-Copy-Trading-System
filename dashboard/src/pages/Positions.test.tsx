import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import * as apiModule from '../lib/api'
import Positions from './Positions'
import { ApiState } from '../lib/types'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function setRole(role: Role) {
  useOrgMock.mockReturnValue(mockUseOrg(role))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const mockApiState: ApiState = {
  accounts: {},
  master_positions: [
    {
      position_id: 1001,
      symbol_id: 1,
      symbol: 'EURUSD',
      side: 'BUY',
      volume: 100000,
      price: 1.0950,
      label: 'long-eur',
      pnl_quote: 500,
      current_price: 1.1,
      volume_lots: '1.0',
      copies: [
        {
          slave_account_id: 2001,
          slave_position_id: 5001,
          slave_volume: 100000,
          status: 'active',
          volume_lots: '1.0',
        },
        {
          slave_account_id: 2002,
          slave_position_id: 5002,
          slave_volume: 100000,
          status: 'failed',
          error: 'Insufficient margin',
          volume_lots: '1.0',
        },
      ],
    },
  ],
  pending_orders: [
    {
      order_id: 3001,
      symbol_id: 2,
      symbol: 'GBPUSD',
      volume: 50000,
      label: 'buy-gbp-pending',
      volume_lots: '0.5',
      copies: [],
    },
  ],
  drift: [
    {
      id: 'drift-1',
      kind: 'orphan_slave_position',
      account_id: 2003,
      position_id: 5003,
      detail: 'Slave position 5003 on account 2003 has no mapped master (copy:m1001)',
    },
    {
      id: 'drift-2',
      kind: 'orphan_slave_position',
      account_id: 2001,
      position_id: 5004,
      detail: 'Slave position 5004 on account 2001 has no mapped master (copy:m1001)',
    },
  ],
}

test('renders master positions with lots and pnl', async () => {
  setRole('owner')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/EURUSD/i)).toBeInTheDocument()
  })

  // Check for volume lots
  expect(screen.getByText('BUY')).toBeInTheDocument()
  // Check for P&L
  const pnlElement = screen.getByText('+500.00')
  expect(pnlElement).toBeInTheDocument()
})

test('per-slave copy status with slippage is always visible', async () => {
  setRole('owner')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/EURUSD/i)).toBeInTheDocument()
  })


  await waitFor(() => {
    // The table should show the slave account ID
    expect(screen.getByText('2001')).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
    // When fill_price is not provided by backend, show "—" for both fill price and slippage
    const dashElements = screen.getAllByText('—')
    expect(dashElements.length).toBeGreaterThan(0)
  })
})

test('failed copy shows error text', async () => {
  setRole('owner')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/EURUSD/i)).toBeInTheDocument()
  })


  await waitFor(() => {
    expect(screen.getByText(/Insufficient margin/)).toBeInTheDocument()
  })
})

test('drift item close-orphan confirms then POSTs', async () => {
  setRole('trader')
  const apiSpy = vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Slave position 5003/)).toBeInTheDocument()
  })

  const closeOrphanButtons = screen.getAllByRole('button', { name: /close orphan/i })
  await userEvent.click(closeOrphanButtons[0])

  await waitFor(() => {
    expect(confirmSpy).toHaveBeenCalled()

    const postCalls = apiSpy.mock.calls.filter(
      (call) => call[0] === 1 && call[1] === 'drift/close-orphan'
    )
    expect(postCalls.length).toBeGreaterThan(0)
  })
})

test('adopt posts master_position_id', async () => {
  setRole('admin')
  const apiSpy = vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Slave position 5003/)).toBeInTheDocument()
  })

  const adoptButtons = screen.getAllByRole('button', { name: /adopt/i })
  await userEvent.click(adoptButtons[0])

  await waitFor(() => {
    const adoptCalls = apiSpy.mock.calls.filter(
      (call) => call[0] === 1 && call[1] === 'drift/adopt'
    )
    expect(adoptCalls.length).toBeGreaterThan(0)

    if (adoptCalls.length > 0) {
      const call = adoptCalls[0]
      const body = call[2]?.body
      if (typeof body === 'string') {
        const parsed = JSON.parse(body)
        expect(parsed).toHaveProperty('id')
        expect(parsed).toHaveProperty('master_position_id')
        // Should parse master_position_id from label (copy:m1001)
        expect(parsed.master_position_id).toBe(1001)
      }
    }
  })
})

// ---------- Task 17: role gating ----------

test('viewer sees no close-orphan, adopt, or dismiss buttons', async () => {
  setRole('viewer')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Slave position 5003/)).toBeInTheDocument()
  })

  expect(screen.queryByRole('button', { name: /close orphan/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /adopt/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /dismiss/i })).not.toBeInTheDocument()
})

test('trader sees close-orphan (trade) but not adopt/dismiss (control)', async () => {
  setRole('trader')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Slave position 5003/)).toBeInTheDocument()
  })

  expect(screen.getAllByRole('button', { name: /close orphan/i }).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: /adopt/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /dismiss/i })).not.toBeInTheDocument()
})

test('admin sees close-orphan, adopt, and dismiss buttons', async () => {
  setRole('admin')
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Slave position 5003/)).toBeInTheDocument()
  })

  expect(screen.getAllByRole('button', { name: /close orphan/i }).length).toBeGreaterThan(0)
  expect(screen.getAllByRole('button', { name: /adopt/i }).length).toBeGreaterThan(0)
  expect(screen.getAllByRole('button', { name: /dismiss/i }).length).toBeGreaterThan(0)
})

test('slave copy rows show their own live P&L from the state snapshot', async () => {
  setRole('owner')
  const stateWithSlaveBooks: ApiState = {
    ...mockApiState,
    accounts: {
      '2001': {
        balance: 5000, equity: 5012.34, open_pnl: 12.34,
        positions: [
          { position_id: 5001, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
            volume: 100000, entry_price: 1.0951, pnl_quote: 12.34,
            current_price: 1.09635 },
        ],
      },
    },
  }
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(stateWithSlaveBooks)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await screen.findByText('EURUSD')

  // The filled copy carries its live P&L; the failed one shows a dash.
  expect(await screen.findByText('+12.34')).toBeInTheDocument()
  expect(screen.getByText('Live P&L')).toBeInTheDocument()
})

test('master rows and copy rows show the live current price', async () => {
  setRole('owner')
  const stateWithPrices: ApiState = {
    ...mockApiState,
    accounts: {
      '2001': {
        balance: 5000, equity: 5012.34, open_pnl: 12.34,
        positions: [
          { position_id: 5001, symbol_id: 1, symbol: 'EURUSD', side: 'BUY',
            volume: 100000, entry_price: 1.0951, pnl_quote: 12.34,
            current_price: 1.09635 },
        ],
      },
    },
  }
  vi.spyOn(apiModule, 'orgApi').mockResolvedValue(stateWithPrices)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  // Master position marks at its tracker quote; the copy at its own
  // account's quote.
  expect(await screen.findByText('1.10000')).toBeInTheDocument()
  expect(await screen.findByText('1.09635')).toBeInTheDocument()

  // The 2002 copy has no snapshot entry: its Current cell must be an em
  // dash, never a fabricated number.
  const dashRow = screen.getByText('2002').closest('tr')!
  const dashCell = dashRow.querySelector('td[data-label="Current"]')!
  expect(dashCell.textContent).toBe('—')
  expect(dashCell.className).toContain('text-ink-faint')

  // The live value stands out in the brand tone.
  expect(screen.getByText('1.10000').className).toContain('text-brand')
})
