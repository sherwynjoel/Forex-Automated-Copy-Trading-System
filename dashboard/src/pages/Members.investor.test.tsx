import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Members from './Members'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('owner'))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/members')) {
      return jsonResponse([{ user_id: 1, email: 'user@example.com', display_name: 'Test User',
                             role: 'owner', joined_at: '2026-09-01T00:00:00Z' }])
    }
    if (url.includes('/invites')) return jsonResponse([])
    return jsonResponse({})
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('investor is offered as an invite role and as an assignable role', async () => {
  render(<MemoryRouter><Members /></MemoryRouter>)
  const invite = await screen.findByLabelText('Invite role')
  expect(Array.from((invite as HTMLSelectElement).options).map((o) => o.value))
    .toContain('investor')
  const assign = await screen.findByLabelText('Role for user@example.com')
  expect(Array.from((assign as HTMLSelectElement).options).map((o) => o.value))
    .toContain('investor')
})
