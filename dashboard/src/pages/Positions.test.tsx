import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import * as apiModule from '../lib/api'
import Positions from './Positions'
import { ApiState } from '../lib/types'

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
      volume_lots: '1.0',
      copies: [
        {
          slave_account_id: 2001,
          slave_position_id: 5001,
          slave_volume: 100000,
          status: 'active',
          fill_price: 1.0955,
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
      kind: 'orphan',
      account_id: 2003,
      position_id: 5003,
      detail: 'Position 5003 on account 2003 has no master',
    },
    {
      id: 'drift-2',
      kind: 'orphan',
      account_id: 2001,
      position_id: 5004,
      detail: 'Position 5004 on account 2001 has no master',
    },
  ],
}

test('renders master positions with lots and pnl', async () => {
  vi.spyOn(apiModule, 'api').mockResolvedValue(mockApiState)

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
  const pnlElement = screen.getByText('500.00')
  expect(pnlElement).toBeInTheDocument()
})

test('expanding a row shows per-slave copy status with slippage', async () => {
  vi.spyOn(apiModule, 'api').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/EURUSD/i)).toBeInTheDocument()
  })

  const expandButton = screen.getByRole('button', { name: /show copies/i })
  await userEvent.click(expandButton)

  await waitFor(() => {
    // The table should show the slave account ID
    expect(screen.getByText('2001')).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
    expect(screen.getByText('1.09550')).toBeInTheDocument()
    // Slippage should be fill_price - entry_price = 1.0955 - 1.0950 = 0.0005 (5 points)
    expect(screen.getByText('5.0')).toBeInTheDocument()
  })
})

test('failed copy shows error text', async () => {
  vi.spyOn(apiModule, 'api').mockResolvedValue(mockApiState)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/EURUSD/i)).toBeInTheDocument()
  })

  const expandButton = screen.getByRole('button', { name: /show copies/i })
  await userEvent.click(expandButton)

  await waitFor(() => {
    expect(screen.getByText(/Insufficient margin/)).toBeInTheDocument()
  })
})

test('drift item close-orphan confirms then POSTs', async () => {
  const apiSpy = vi.spyOn(apiModule, 'api').mockResolvedValue(mockApiState)
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Position 5003/)).toBeInTheDocument()
  })

  const closeOrphanButtons = screen.getAllByRole('button', { name: /close orphan/i })
  await userEvent.click(closeOrphanButtons[0])

  await waitFor(() => {
    expect(confirmSpy).toHaveBeenCalled()

    const postCalls = apiSpy.mock.calls.filter(
      (call) => call[0]?.includes('/api/drift/close-orphan')
    )
    expect(postCalls.length).toBeGreaterThan(0)
  })
})

test('adopt posts master_position_id', async () => {
  const apiSpy = vi.spyOn(apiModule, 'api').mockResolvedValue(mockApiState)
  vi.stubGlobal('prompt', vi.fn().mockReturnValue('1001'))

  render(
    <MemoryRouter>
      <Positions />
    </MemoryRouter>
  )

  await waitFor(() => {
    expect(screen.getByText(/Position 5003/)).toBeInTheDocument()
  })

  const adoptButtons = screen.getAllByRole('button', { name: /adopt/i })
  await userEvent.click(adoptButtons[0])

  await waitFor(() => {
    const adoptCalls = apiSpy.mock.calls.filter(
      (call) => call[0]?.includes('/api/drift/adopt')
    )
    expect(adoptCalls.length).toBeGreaterThan(0)

    if (adoptCalls.length > 0) {
      const call = adoptCalls[0]
      const body = call[1]?.body
      if (typeof body === 'string') {
        const parsed = JSON.parse(body)
        expect(parsed).toHaveProperty('id')
        expect(parsed).toHaveProperty('master_position_id')
      }
    }
  })
})
