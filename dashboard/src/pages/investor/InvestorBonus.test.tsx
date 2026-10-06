import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorBonus from './InvestorBonus'
import { mockUseOrg } from '../../test/orgMock'
import { bonusFixture, summaryFixture } from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary = summaryFixture({
  wallets: { ...summaryFixture().wallets, credit: { balance: 75, on_hold: 25, available: 50 } },
})
const reversed = bonusFixture({ id: 5, source: 'manual', source_id: null, amount: -5, note: 'Reversed' })
const deposit = bonusFixture({ id: 4 })

function mockRoutes(opts: { slowAll?: Promise<Response> } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.includes('/investor/bonuses?')) return jsonResponse([deposit])
    if (url.endsWith('/investor/bonuses')) return opts.slowAll ?? jsonResponse([reversed, deposit])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the Credit wallet and the history, filtered by source and dates', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorBonus /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Bonus' })).toBeInTheDocument()
  expect(await screen.findByText('75.00 USD')).toBeInTheDocument()
  expect(screen.getByText('50.00 USD')).toBeInTheDocument()
  expect(screen.getByText('Reversed')).toBeInTheDocument()
  expect(screen.getByText('deposit #12')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Move to a trading account' }))
    .toHaveAttribute('href', '/org/1/invest/transfer')
  await userEvent.selectOptions(screen.getByLabelText('Source'), 'deposit')
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-01' } })
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(screen.queryByText('Reversed')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investor/bonuses?source=deposit&from=2026-09-01'))).toBe(true)
})

test('a late answer for an older filter never lands', async () => {
  let release!: (r: Response) => void
  mockRoutes({ slowAll: new Promise<Response>((res) => { release = res }) })
  render(<MemoryRouter><InvestorBonus /></MemoryRouter>)
  await userEvent.selectOptions(await screen.findByLabelText('Source'), 'deposit')
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(await screen.findByText('deposit #12')).toBeInTheDocument()
  await act(async () => { release(jsonResponse([reversed, deposit])) })
  expect(screen.queryByText('Reversed')).not.toBeInTheDocument()
})
