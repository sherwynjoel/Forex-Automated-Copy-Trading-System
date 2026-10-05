import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorSecurity from './InvestorSecurity'
import { mockUseOrg } from '../../test/orgMock'
import { signInFixture } from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { wrongMpinOnce?: boolean } = {}) {
  let passwordPosts = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/me/sign-ins')) return jsonResponse([signInFixture()])
    if (url === '/api/me/password' && init?.method === 'POST') {
      passwordPosts += 1
      if (opts.wrongMpinOnce && passwordPosts === 1) {
        return jsonResponse({ detail: 'Invalid MPIN', attempts_left: 4 }, 401)
      }
      return jsonResponse(null, 204)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function enterPin(dialog: HTMLElement, pin: string) {
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the Security page shows the login forms and the sign-in history', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorSecurity /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Security' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(await screen.findByText('Chrome on Windows')).toBeInTheDocument()
  expect(document.title).toBe('Security · MirrorFleet')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/me/sign-ins?limit=50')).toBe(true)
})

test('a password change asks for the MPIN; a wrong one stays in the dialog', async () => {
  const fetchMock = mockRoutes({ wrongMpinOnce: true })
  render(<MemoryRouter><InvestorSecurity /></MemoryRouter>)
  await screen.findByText('Chrome on Windows')
  await userEvent.type(screen.getByLabelText('Current password'), 'old-password-x')
  await userEvent.type(screen.getByLabelText('New password'), 'new-password-y')
  await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
  const dialog = await screen.findByRole('dialog', { name: 'Change your password?' })
  await enterPin(dialog, '000000')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
  expect(await within(dialog).findByText('Wrong MPIN, 4 tries left')).toBeInTheDocument()
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
  expect(await screen.findByText(/signed out/)).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const posts = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/me/password')
  expect(JSON.parse(String((posts[1][1] as RequestInit).body))).toEqual({
    current_password: 'old-password-x', new_password: 'new-password-y', mpin: '123456',
  })
})
