import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Members from './Members'
import type { Role } from '../lib/roles'

const { useOrgMock, navigateMock } = vi.hoisted(() => ({
  useOrgMock: vi.fn(),
  navigateMock: vi.fn(),
}))

vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const baseMembers = [
  { user_id: 1, email: 'owner@x.com', display_name: 'Owner O', role: 'owner', joined_at: '2026-01-01T00:00:00Z' },
  { user_id: 2, email: 'trader@x.com', display_name: 'Trader T', role: 'trader', joined_at: '2026-01-02T00:00:00Z' },
  { user_id: 3, email: 'viewer@x.com', display_name: 'Viewer V', role: 'viewer', joined_at: '2026-01-03T00:00:00Z' },
]

const baseInvites = [
  { id: 10, role: 'viewer', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-02-08T00:00:00Z', consumed: false },
]

function makeOrgValue(role: Role, meUserId = 1) {
  return {
    orgId: 1,
    role,
    org: { id: 1, name: 'Acme', role },
    me: {
      user: { id: meUserId, email: 'owner@x.com', display_name: 'Owner O' },
      orgs: [{ id: 1, name: 'Acme', role }],
    },
    refreshMe: vi.fn(),
  }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Exact METHOD+url matching so `/api/orgs/1` overrides never swallow the
 * nested `/api/orgs/1/members/...` requests (substring matching would). */
function mockRoutes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method || 'GET').toUpperCase()
    const key = `${method} ${url}`
    if (key in overrides) return overrides[key](init)
    if (method === 'GET' && url === '/api/orgs/1/members') return jsonResponse(baseMembers)
    if (method === 'GET' && url === '/api/orgs/1/invites') return jsonResponse(baseInvites)
    if (method === 'PATCH' && url.startsWith('/api/orgs/1/members/')) return jsonResponse({})
    if (method === 'DELETE' && url.startsWith('/api/orgs/1/members/')) return jsonResponse(null, 204)
    if (method === 'DELETE' && url.startsWith('/api/orgs/1/invites/')) return jsonResponse(null, 204)
    if (method === 'PATCH' && url === '/api/orgs/1') return jsonResponse({ id: 1, name: 'Acme' })
    if (method === 'DELETE' && url === '/api/orgs/1') return jsonResponse(null, 204)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderMembers() {
  return render(
    <MemoryRouter>
      <Members />
    </MemoryRouter>
  )
}

test('lists members with roles', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })
  expect(screen.getByText('trader@x.com')).toBeInTheDocument()
  expect(screen.getByText('viewer@x.com')).toBeInTheDocument()
  expect(screen.getByLabelText('Role for owner@x.com')).toHaveValue('owner')
  expect(screen.getByLabelText('Role for trader@x.com')).toHaveValue('trader')
})

test('owner can change a role via the role select', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  const fetchMock = mockRoutes()
  renderMembers()

  const select = await screen.findByLabelText('Role for trader@x.com')
  await userEvent.selectOptions(select, 'admin')

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/members/2',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ role: 'admin' }) })
    )
  })
})

test('non-owner sees read-only roles and no invite form', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer', 3))
  mockRoutes()
  renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })
  expect(screen.queryByLabelText(/^Role for /)).not.toBeInTheDocument()
  expect(screen.getByText('owner')).toBeInTheDocument()
  expect(screen.queryByText('Invites')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Invite role')).not.toBeInTheDocument()
  expect(screen.queryByText('Organization')).not.toBeInTheDocument()
  // The viewer's own row shows "Leave"; the others show nothing.
  expect(screen.getByRole('button', { name: 'Leave' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
})

test('admin can create an invite and sees the link once', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin', 4))
  mockRoutes({
    'POST /api/orgs/1/invites': () =>
      jsonResponse({ id: 99, role: 'viewer', token: 'abc123tok', expires_at: '2026-03-01T00:00:00Z' }, 201),
  })
  renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })

  await userEvent.click(screen.getByRole('button', { name: /create invite link/i }))

  const link = await screen.findByText(/\/join\/abc123tok/)
  expect(link).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument()
})

test('shows the last-owner error from the server', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes({
    'PATCH /api/orgs/1/members/2': () =>
      jsonResponse({ detail: 'An org must keep at least one owner' }, 409),
  })
  renderMembers()

  const select = await screen.findByLabelText('Role for trader@x.com')
  await userEvent.selectOptions(select, 'viewer')

  await waitFor(() => {
    expect(screen.getByText(/must keep at least one owner/i)).toBeInTheDocument()
  })
})

test('owner can rename the org', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  const fetchMock = mockRoutes()
  renderMembers()

  const input = await screen.findByLabelText('Organization name')
  await userEvent.clear(input)
  await userEvent.type(input, 'Acme Renamed')
  await userEvent.click(screen.getByRole('button', { name: /^rename$/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ name: 'Acme Renamed' }) })
    )
  })
})

test('owner sees delete-org with type-to-confirm; others do not', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  const { unmount } = renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })

  const deleteButton = screen.getByRole('button', { name: /delete organization/i })
  await userEvent.click(deleteButton)

  const dialog = screen.getByRole('dialog')
  await userEvent.type(within(dialog).getByLabelText(/type delete to continue/i), 'DELETE')
  await userEvent.click(within(dialog).getByRole('button', { name: /^delete organization$/i }))

  await waitFor(() => {
    expect(navigateMock).toHaveBeenCalledWith('/welcome')
  })
  unmount()

  useOrgMock.mockReturnValue(makeOrgValue('admin', 4))
  mockRoutes()
  renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })
  expect(screen.queryByRole('button', { name: /delete organization/i })).not.toBeInTheDocument()
  expect(screen.queryByText('Organization')).not.toBeInTheDocument()
})
