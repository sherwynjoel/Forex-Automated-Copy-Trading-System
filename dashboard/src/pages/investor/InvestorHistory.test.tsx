import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorHistory from './InvestorHistory'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const deal = { deal_id: 1, order_id: 1, position_id: 7, symbol_id: 41, symbol: 'XAUUSD',
               side: 'SELL', volume: 100, filled_volume: 100, volume_lots: '0.01',
               execution_price: 4360.0, status: 'FILLED', commission: -0.07,
               create_timestamp: 1758620000000, execution_timestamp: 1758620000000,
               close: { entry_price: 4350.0, gross_profit: 10.0, swap: -0.1, commission: -0.07,
                        balance: 5009.83, closed_volume: 100, closed_volume_lots: '0.01' } }

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists closed deals for the last week and pages earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(screen.getByText('9.83')).toBeInTheDocument()
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  const first = String(fetchMock.mock.calls[0][0])
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  const second = String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0])
  const toOf = (u: string) => Number(new URL(u, 'http://x').searchParams.get('to'))
  expect(toOf(first) - toOf(second)).toBe(7 * 24 * 3600 * 1000)
})
