import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import BonusRulesCard from './BonusRulesCard'
import { bonusRulesFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(refuse?: { status: number; body: unknown }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/bonus-rules') && init?.method === 'PUT') {
      if (refuse) return jsonResponse(refuse.body, refuse.status)
      const body = JSON.parse(init.body as string)
      return jsonResponse(bonusRulesFixture({
        signup_enabled: body.signup_enabled, signup_amount: Number(body.signup_amount),
        deposit_cap: body.deposit_cap == null ? null : Number(body.deposit_cap) }))
    }
    if (url.endsWith('/bonus-rules')) return jsonResponse(bonusRulesFixture())
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('loads the rules and saves them as typed', async () => {
  const fetchMock = mockRoutes()
  render(<BonusRulesCard orgId={1} control />)
  expect(await screen.findByLabelText('Deposit bonus on')).toBeChecked()
  expect(screen.getByLabelText('Deposit bonus %')).toHaveValue('10')
  expect(screen.getByLabelText('Deposit bonus cap')).toHaveValue('100')
  await userEvent.click(screen.getByLabelText('Welcome bonus on'))
  await userEvent.clear(screen.getByLabelText('Welcome bonus amount'))
  await userEvent.type(screen.getByLabelText('Welcome bonus amount'), '50')
  await userEvent.clear(screen.getByLabelText('Deposit bonus cap'))
  await userEvent.click(screen.getByRole('button', { name: 'Save bonus rules' }))
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
      signup_enabled: true, signup_amount: '50', kyc_enabled: false, kyc_amount: '0',
      deposit_enabled: true, deposit_pct: '10', deposit_cap: null })
  })
  expect(await screen.findByText('Bonus rules saved')).toBeInTheDocument()
  expect(screen.getByLabelText('Deposit bonus cap')).toHaveValue('')
})

test("the server's refusal shows inside the card", async () => {
  mockRoutes({ status: 400, body: { detail: 'signup_amount must be above 0 while the signup rule is on' } })
  render(<BonusRulesCard orgId={1} control />)
  await userEvent.click(await screen.findByLabelText('Welcome bonus on'))
  await userEvent.click(screen.getByRole('button', { name: 'Save bonus rules' }))
  expect(await screen.findByText('signup_amount must be above 0 while the signup rule is on')).toBeInTheDocument()
})

test('a viewer sees the rules but cannot change them', async () => {
  mockRoutes()
  render(<BonusRulesCard orgId={1} control={false} />)
  expect(await screen.findByLabelText('Deposit bonus %')).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Save bonus rules' })).not.toBeInTheDocument()
})
