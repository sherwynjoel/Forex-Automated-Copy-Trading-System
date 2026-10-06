import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Notifications from './Notifications'
import { mockUseOrg } from '../test/orgMock'
import { notificationFixture } from '../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const first = notificationFixture({ id: 31 })
const second = notificationFixture({
  id: 30, topic: 'support', title: 'New reply on ticket #7: Deposits', body: 'We are checking',
  link: '/org/1/invest/support?ticket=7', read_at: '2026-10-01T10:00:00Z',
})
const older = notificationFixture({
  id: 12, topic: 'bonus', title: 'You received a 50.00 USD welcome bonus', link: null,
  read_at: '2026-09-01T10:00:00Z',
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=50&before=30')) {
      return jsonResponse({ notifications: [older], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications?limit=50')) {
      return jsonResponse({ notifications: [first, second], has_more: true, next_before: 30 })
    }
    if (url.endsWith('/notifications/31/read') && init?.method === 'POST') {
      return jsonResponse({ ...first, read_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/org/1/invest/notifications']}>
      <Routes>
        <Route path="/org/1/invest/notifications" element={<Notifications />} />
        <Route path="/org/1/invest/deposit" element={<div>deposit page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists newest first with the read state and loads more', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  expect(await screen.findByRole('heading', { level: 1, name: 'Notifications' })).toBeInTheDocument()
  expect(await screen.findByText('Your deposit of 250.00 USD was confirmed')).toBeInTheDocument()
  expect(screen.getAllByText('Unread')).toHaveLength(1)
  expect(screen.getByText('Support')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('You received a 50.00 USD welcome bonus')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('before=30'))).toBe(true)
})

test('a click marks the row read and follows its link', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
  expect(await screen.findByText('deposit page')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/31/read') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
})

test('Mark all read clears every unread badge; the button stays enabled (read-all is idempotent)', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
  await waitFor(() => expect(screen.queryByText('Unread')).not.toBeInTheDocument())
  // Rows are still on screen, so the button stays usable -- it is never
  // derived from whether any row still looks unread.
  expect(screen.getByRole('button', { name: 'Mark all read' })).toBeEnabled()
  expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/notifications/read-all'))).toHaveLength(1)
})

test('with every visible row already read, Mark all read stays enabled and still posts', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=50')) {
      return jsonResponse({ notifications: [second, older], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await screen.findByText('New reply on ticket #7: Deposits')
  expect(screen.queryByText('Unread')).not.toBeInTheDocument()
  const button = screen.getByRole('button', { name: 'Mark all read' })
  expect(button).toBeEnabled()
  await userEvent.click(button)
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/read-all') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
})

test('a late answer for the previous org never lands', async () => {
  let release!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input).startsWith('/api/orgs/1/')) return new Promise<Response>((res) => { release = res })
    return Promise.resolve(jsonResponse({ notifications: [second], has_more: false, next_before: null }))
  }))
  useOrgMock.mockReturnValue(mockUseOrg('investor', 1))
  const view = render(<MemoryRouter><Notifications /></MemoryRouter>)
  useOrgMock.mockReturnValue(mockUseOrg('investor', 2))
  view.rerender(<MemoryRouter><Notifications /></MemoryRouter>)
  expect(await screen.findByText('New reply on ticket #7: Deposits')).toBeInTheDocument()
  await act(async () => {
    release(jsonResponse({ notifications: [first], has_more: false, next_before: null }))
  })
  expect(screen.queryByText('Your deposit of 250.00 USD was confirmed')).not.toBeInTheDocument()
})
