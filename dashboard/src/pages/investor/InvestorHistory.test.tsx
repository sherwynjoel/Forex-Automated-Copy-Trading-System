import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorHistory from './InvestorHistory'
import { mockUseOrg } from '../../test/orgMock'
import { accountSummaryFixture, summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'

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
    if (url.endsWith('/investor/summary')) return jsonResponse(summaryFixture())
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
})
// Unmount before resetting the hide-balances store (see Money.test.tsx):
// otherwise the broadcast from setHidden(false) updates a still-mounted
// Money outside act().
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setHidden(false) })

/** The innermost element whose whole text reads `text` -- the net total is
 *  a Money figure inside a sentence, so its text spans two elements. */
function wholeText(text: string | RegExp) {
  const hit = (t: string | null | undefined) =>
    typeof text === 'string' ? t === text : text.test(t ?? '')
  return (_content: string, el: Element | null) =>
    el != null && hit(el.textContent) && !Array.from(el.children).some((c) => hit(c.textContent))
}

const historyCalls = () => (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
  .map(([u]) => String(u)).filter((u) => u.includes('/investor/history/deals'))

test('lists closed deals for the last week and pages earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  // The row's net and the window's net total: one deal, one figure twice.
  expect(screen.getAllByText('9.83 USD')).toHaveLength(2)
  expect(screen.getByText('10.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
  expect(document.title).toBe('History · MirrorFleet')
  const first = historyCalls()[0]
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  await waitFor(() => expect(historyCalls()).toHaveLength(2))
  const second = historyCalls()[1]
  const toOf = (u: string) => Number(new URL(u, 'http://x').searchParams.get('to'))
  expect(toOf(first) - toOf(second)).toBe(7 * 24 * 3600 * 1000)
})

test('the total names the real window, not "this week", once paged back', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText(wholeText('9.83 USD net, last 7 days'))).toBeInTheDocument()
  expect(screen.queryByText(/this week/i)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  expect(await screen.findByText(wholeText(/^9\.83 USD net, .+ – .+$/))).toBeInTheDocument()
  expect(screen.queryByText(/last 7 days/)).not.toBeInTheDocument()
})

test('Later is disabled at the current week and comes back after paging earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  await screen.findByText('XAUUSD')
  const later = screen.getByRole('button', { name: 'Later' })
  expect(later).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  expect(later).toBeEnabled()
  await userEvent.click(later)
  expect(later).toBeDisabled()
  await screen.findByText('XAUUSD')
})

test('hiding balances masks every closed-trade figure and drops the profit/loss colour', async () => {
  setHidden(true)
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  // The net total, and the row's gross, swap + fees and net.
  const masked = screen.getAllByRole('img', { name: 'Hidden amount' })
  expect(masked).toHaveLength(4)
  for (const m of masked) expect(m.className).not.toMatch(/text-(profit|loss)/)
  expect(screen.getByText(wholeText('•••• net, last 7 days'))).toBeInTheDocument()
  for (const figure of [/9\.83/, /10\.00/, /0\.17/]) {
    expect(screen.queryByText(figure)).not.toBeInTheDocument()
  }
  // No toned wrapper either: a coloured cell or sentence gives the sign away.
  expect(document.querySelector('.text-profit, .text-loss')).toBeNull()
})

test('shown, the net figures keep their profit/loss colour', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  await screen.findByText('XAUUSD')
  expect(screen.getAllByText('9.83 USD').every((el) => el.classList.contains('text-profit'))).toBe(true)
})

test('the history follows the picked account', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(summaryFixture({ accounts: [
        accountSummaryFixture({ account_id: 1001, mt5_login: 5001 }),
        accountSummaryFixture({ account_id: 1002, mt5_login: 6002 })] }))
    }
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
  render(<MemoryRouter initialEntries={['/org/1/invest/history?account=1002']}><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(historyCalls().at(-1)).toMatch(/&account_id=1002$/)
  await userEvent.selectOptions(screen.getByLabelText('Trading account'), '1001')
  await waitFor(() => expect(historyCalls().at(-1)).toMatch(/&account_id=1001$/))
})

test("a stale deals response for the account you switched away from doesn't overwrite the picked one", async () => {
  // Deferred per account: each account's `investor/history/deals` call is
  // resolved by hand, in whichever order the test chooses, instead of
  // whichever order orgApi happens to call fetch in.
  const resolvers: Record<string, (r: Response) => void> = {}
  function dealsFor(accountId: number, symbol: string) {
    return new Promise<Response>((resolve) => { resolvers[String(accountId)] = resolve })
      .then(() => jsonResponse({ deals: [{ ...deal, deal_id: accountId, symbol }], has_more: false }))
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(summaryFixture({ accounts: [
        accountSummaryFixture({ account_id: 1001, mt5_login: 5001 }),
        accountSummaryFixture({ account_id: 1002, mt5_login: 6002 })] }))
    }
    if (url.includes('account_id=1001')) return dealsFor(1001, 'XAUUSD')
    if (url.includes('account_id=1002')) return dealsFor(1002, 'EURUSD')
    return jsonResponse({})
  }))
  render(<MemoryRouter initialEntries={['/org/1/invest/history?account=1001']}><InvestorHistory /></MemoryRouter>)
  // Wait for account 1001's deals call to have gone out, then switch to
  // 1002 before it resolves -- 1001's response is now stale.
  await waitFor(() => expect(historyCalls().some((u) => u.includes('account_id=1001'))).toBe(true))
  await userEvent.selectOptions(await screen.findByLabelText('Trading account'), '1002')
  await waitFor(() => expect(historyCalls().some((u) => u.includes('account_id=1002'))).toBe(true))
  // The picked account's (1002's) response lands first...
  resolvers['1002'](new Response())
  expect(await screen.findByText('EURUSD')).toBeInTheDocument()
  // ...then the stale 1001 response lands after. It must not overwrite 1002's
  // row. act()'s flush (a macrotask boundary) gives any `setDeals` triggered
  // by that stale response every chance to land before the assertion below,
  // so a still-missing guard is caught here rather than hidden by a
  // too-early check.
  await act(async () => {
    resolvers['1001'](new Response())
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  expect(screen.getByText('EURUSD')).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
})
