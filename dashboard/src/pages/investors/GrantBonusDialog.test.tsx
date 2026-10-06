import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import GrantBonusDialog from './GrantBonusDialog'
import { bonusFixture, investorRowFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const investor = investorRowFixture({
  user_id: 5, display_name: 'Ada Investor', balances: { main: 0, credit: 20, pamm: 0, social: 0 },
})

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('pays a bonus with the admin MPIN', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse(bonusFixture({ id: 9, source: 'manual', source_id: null, amount: 25, note: 'Promo' }), 201))
  vi.stubGlobal('fetch', fetchMock)
  const onGranted = vi.fn()
  render(<GrantBonusDialog orgId={1} investor={investor} onCancel={vi.fn()} onGranted={onGranted} />)
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '25')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'Promo')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  await waitFor(() => expect(onGranted).toHaveBeenCalledWith(expect.objectContaining({ id: 9, amount: 25 })))
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/investors/5/bonuses')
  expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string))
    .toEqual({ amount: '25', note: 'Promo', mpin: '123456' })
})

test('a zero amount never reaches the server; a refused claw-back shows its reason', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(
    { detail: 'a claw-back cannot take the Credit wallet below zero (available 20.00)' }, 400))
  vi.stubGlobal('fetch', fetchMock)
  render(<GrantBonusDialog orgId={1} investor={investor} onCancel={vi.fn()} onGranted={vi.fn()} />)
  const dialog = await screen.findByRole('dialog', { name: 'Grant Ada Investor a bonus' })
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '0')
  await userEvent.type(within(dialog).getByLabelText('Bonus note'), 'x')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await within(dialog).findByText('amount must not be zero')).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()
  await userEvent.clear(within(dialog).getByLabelText('Bonus amount'))
  await userEvent.type(within(dialog).getByLabelText('Bonus amount'), '-25')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Pay bonus' }))
  expect(await within(dialog).findByText(
    'a claw-back cannot take the Credit wallet below zero (available 20.00)')).toBeInTheDocument()
})
