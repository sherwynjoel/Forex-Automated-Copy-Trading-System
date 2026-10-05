import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import NotificationBell from './NotificationBell'
import { notificationFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const unread = notificationFixture({ id: 31 })
const read = notificationFixture({
  id: 30, topic: 'identity', title: 'Your identity verification was approved',
  link: '/org/1/invest/profile', read_at: '2026-10-01T10:00:00Z',
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=8')) {
      return jsonResponse({ notifications: [unread, read], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications/31/read') && init?.method === 'POST') {
      return jsonResponse({ ...unread, read_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderBell(count: number | undefined = 1) {
  const onChange = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/1']}>
      <Routes>
        <Route path="/org/1" element={
          <NotificationBell orgId={1} pageHref="/org/1/invest/notifications" count={count} onChange={onChange} />
        } />
        <Route path="/org/1/invest/deposit" element={<div>deposit page</div>} />
        <Route path="/org/1/invest/notifications" element={<div>notifications page</div>} />
      </Routes>
    </MemoryRouter>,
  )
  return onChange
}

async function openPanel(name: string) {
  await userEvent.click(screen.getByRole('button', { name }))
  return screen.findByRole('dialog', { name: 'Latest notifications' })
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the bell names the unread count and lists the latest eight', async () => {
  const fetchMock = mockRoutes()
  renderBell(3)
  const panel = await openPanel('Notifications, 3 unread')
  expect(await within(panel).findByText('Your deposit of 250.00 USD was confirmed')).toBeInTheDocument()
  expect(within(panel).getByText('Your identity verification was approved')).toBeInTheDocument()
  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/orgs/1/notifications?limit=8')
  expect(screen.getByRole('button', { name: 'Notifications, 3 unread' })).toHaveAttribute('aria-expanded', 'true')
})

test('with nothing unread the name carries no number', () => {
  mockRoutes()
  renderBell(0)
  expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument()
})

test('a click marks the row read, tells the layout and follows its link', async () => {
  const fetchMock = mockRoutes()
  const onChange = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await userEvent.click(await within(panel).findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
  expect(await screen.findByText('deposit page')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/31/read') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
  expect(onChange).toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('Mark all read empties the unread state; See all goes to the page', async () => {
  const fetchMock = mockRoutes()
  const onChange = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.click(within(panel).getByRole('button', { name: 'Mark all read' }))
  await waitFor(() => expect(onChange).toHaveBeenCalled())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/notifications/read-all'))).toBe(true)
  expect(within(panel).getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  await userEvent.click(within(panel).getByRole('link', { name: 'See all notifications' }))
  expect(await screen.findByText('notifications page')).toBeInTheDocument()
})
