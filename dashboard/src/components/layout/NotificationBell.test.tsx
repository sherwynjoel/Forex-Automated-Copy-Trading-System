import { act, render, screen, waitFor, within } from '@testing-library/react'
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

function bellTree(count: number | undefined, onChange: () => void) {
  return (
    <MemoryRouter initialEntries={['/org/1']}>
      <Routes>
        <Route path="/org/1" element={
          <NotificationBell orgId={1} pageHref="/org/1/invest/notifications" count={count} onChange={onChange} />
        } />
        <Route path="/org/1/invest/deposit" element={<div>deposit page</div>} />
        <Route path="/org/1/invest/notifications" element={<div>notifications page</div>} />
      </Routes>
    </MemoryRouter>
  )
}

function renderBell(count: number | undefined = 1) {
  const onChange = vi.fn()
  const view = render(bellTree(count, onChange))
  return { onChange, setCount: (c: number | undefined) => view.rerender(bellTree(c, onChange)) }
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
  const { onChange } = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await userEvent.click(await within(panel).findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
  expect(await screen.findByText('deposit page')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) =>
    String(u).endsWith('/notifications/31/read') && (i as RequestInit | undefined)?.method === 'POST')).toBe(true)
  expect(onChange).toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('Mark all read calls read-all; the badge itself only clears once the authoritative count does', async () => {
  const fetchMock = mockRoutes()
  const { onChange, setCount } = renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.click(within(panel).getByRole('button', { name: 'Mark all read' }))
  await waitFor(() => expect(onChange).toHaveBeenCalled())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/notifications/read-all'))).toBe(true)
  // onChange tells the layout to refresh the authoritative count; once that
  // answer comes back as 0, the parent passes a new `count` prop down.
  setCount(0)
  expect(within(panel).getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  await userEvent.click(within(panel).getByRole('link', { name: 'See all notifications' }))
  expect(await screen.findByText('notifications page')).toBeInTheDocument()
})

test('the authoritative count decides Mark all read, not the fetched latest eight', async () => {
  // count says 3 unread, but every one of the fetched rows is already read
  // (e.g. read on another device) -- the button must still work.
  const bothRead = { ...unread, read_at: '2026-10-01T10:00:00Z' }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/notifications?limit=8')) {
      return jsonResponse({ notifications: [bothRead, read], has_more: false, next_before: null })
    }
    if (url.endsWith('/notifications/read-all') && init?.method === 'POST') return jsonResponse({ updated: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderBell(3)
  const panel = await openPanel('Notifications, 3 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  const markAllRead = within(panel).getByRole('button', { name: 'Mark all read' })
  expect(markAllRead).toBeEnabled()
  await userEvent.click(markAllRead)
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/notifications/read-all'))).toBe(true)
})

test('opening with the keyboard moves focus into the panel', async () => {
  mockRoutes()
  renderBell()
  const trigger = screen.getByRole('button', { name: 'Notifications, 1 unread' })
  act(() => trigger.focus())
  await userEvent.keyboard('{Enter}')
  const panel = await screen.findByRole('dialog', { name: 'Latest notifications' })
  expect(panel).toHaveFocus()
})

test('ArrowDown from the opened panel focuses the first row', async () => {
  mockRoutes()
  renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.keyboard('{ArrowDown}')
  expect(within(panel).getByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
    .toHaveFocus()
})

test('ArrowUp from the opened panel wraps to the last item (See all)', async () => {
  mockRoutes()
  renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.keyboard('{ArrowUp}')
  expect(within(panel).getByRole('link', { name: 'See all notifications' })).toHaveFocus()
})

test('Tab from the last item wraps to the first, rather than escaping into the page behind the panel', async () => {
  mockRoutes()
  renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  const seeAll = await within(panel).findByRole('link', { name: 'See all notifications' })
  act(() => seeAll.focus())
  await userEvent.keyboard('{Tab}')
  expect(within(panel).getByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
    .toHaveFocus()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('Shift+Tab from the first item wraps to the last', async () => {
  mockRoutes()
  renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  const firstRow = await within(panel).findByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ })
  act(() => firstRow.focus())
  await userEvent.keyboard('{Shift>}{Tab}{/Shift}')
  expect(within(panel).getByRole('link', { name: 'See all notifications' })).toHaveFocus()
})

test('Home and End jump to the first and last focusable item', async () => {
  mockRoutes()
  renderBell()
  const panel = await openPanel('Notifications, 1 unread')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  await userEvent.keyboard('{End}')
  expect(within(panel).getByRole('link', { name: 'See all notifications' })).toHaveFocus()
  await userEvent.keyboard('{Home}')
  expect(within(panel).getByRole('button', { name: /Your deposit of 250.00 USD was confirmed/ }))
    .toHaveFocus()
})

test('a disabled Mark all read is skipped by keyboard navigation', async () => {
  mockRoutes()
  renderBell(0)
  const panel = await openPanel('Notifications')
  await within(panel).findByText('Your deposit of 250.00 USD was confirmed')
  expect(within(panel).getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  // Two rows, so Mark all read (disabled) must be skipped between them and "See all".
  await userEvent.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}')
  expect(within(panel).getByRole('link', { name: 'See all notifications' })).toHaveFocus()
})

test('Escape closes the panel and returns focus to the bell', async () => {
  mockRoutes()
  renderBell()
  const trigger = screen.getByRole('button', { name: 'Notifications, 1 unread' })
  await openPanel('Notifications, 1 unread')
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})

test('clicking outside the panel closes it', async () => {
  mockRoutes()
  render(
    <>
      {bellTree(1, vi.fn())}
      <button>Elsewhere</button>
    </>,
  )
  await openPanel('Notifications, 1 unread')
  await userEvent.click(screen.getByRole('button', { name: 'Elsewhere' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
