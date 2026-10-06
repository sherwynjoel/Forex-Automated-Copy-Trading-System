import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import SupportTab from './SupportTab'
import { threadFixture, ticketFixture, ticketMessageFixture } from '../../test/portalFixtures'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const WHEN = '2026-10-05T10:00:00Z'
const queue = [
  ticketFixture({ id: 7, email: 'inv@example.com', display_name: 'Ada Investor', status: 'new' }),
  ticketFixture({ id: 8, subject_label: 'Withdrawals', email: 'bo@example.com', display_name: 'Bo',
                  status: 'open', last_from_desk: true, waiting_on_desk: false }),
  ticketFixture({ id: 9, subject_label: 'Old', email: 'cy@example.com', display_name: 'Cy',
                  status: 'closed', closed_at: WHEN, waiting_on_desk: false }),
]
const thread7 = threadFixture({
  id: 7, email: 'inv@example.com', display_name: 'Ada Investor',
  messages: [ticketMessageFixture({ id: 70, author_name: 'Ada Investor', body: 'My deposit has not arrived.', file_ids: [41] })],
})

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/tickets/7/messages') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'open', last_from_desk: true, waiting_on_desk: false, messages: [
        ...thread7.messages,
        ticketMessageFixture({ id: 71, from_desk: true, author_name: 'Desk Admin', body: 'We are checking.' })] }, 201)
    }
    if (url.endsWith('/tickets/7/close') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'closed', closed_at: WHEN, waiting_on_desk: false })
    }
    if (url.endsWith('/tickets/7')) return jsonResponse(thread7)
    if (url.includes('/tickets?q=')) return jsonResponse([queue[1]])
    if (url.endsWith('/tickets')) return jsonResponse(queue)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('the open view hides closed tickets and marks the ones waiting on the desk', async () => {
  const fetchMock = mockRoutes()
  render(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={null}
                     onDrawerClosed={vi.fn()} />)
  expect(await screen.findByText('#7 Deposits')).toBeInTheDocument()
  expect(screen.getByText('#8 Withdrawals')).toBeInTheDocument()
  expect(screen.queryByText('#9 Old')).not.toBeInTheDocument()
  expect(screen.getAllByText('Waiting on desk')).toHaveLength(1)
  await userEvent.type(screen.getByLabelText('Search tickets'), 'wire')
  await userEvent.click(screen.getByRole('button', { name: 'Search' }))
  await waitFor(() => expect(screen.queryByText('#7 Deposits')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/tickets?q=wire'))).toBe(true)
})

test('the drawer shows the thread, sends a reply and closes the ticket', async () => {
  const fetchMock = mockRoutes()
  const onDone = vi.fn()
  render(<SupportTab orgId={1} control show="all" onDone={onDone} initialTicket={null}
                     onDrawerClosed={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Open ticket 7' }))
  const drawer = await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })
  expect(within(drawer).getByText('My deposit has not arrived.')).toBeInTheDocument()
  expect(within(drawer).getByRole('img', { name: 'Image 1' })).toHaveAttribute('src', '/api/orgs/1/files/41')
  await userEvent.type(within(drawer).getByLabelText('Reply to the investor'), 'We are checking.')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Send reply' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Reply sent'))
  const post = fetchMock.mock.calls.find(([u, i]) =>
    String(u).endsWith('/tickets/7/messages') && (i as RequestInit | undefined)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({ body: 'We are checking.' })
  expect(within(drawer).getByText('Desk Admin (desk)')).toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close ticket' }))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith('Ticket closed'))
  expect(within(drawer).getByText('Closed. The investor can open it again by replying.')).toBeInTheDocument()
})

test('a notification link opens its ticket straight away', async () => {
  mockRoutes()
  render(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={7}
                     onDrawerClosed={vi.fn()} />)
  expect(await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })).toBeInTheDocument()
})

test('a later link is followed while mounted, and closing the drawer reports it', async () => {
  mockRoutes()
  const onDrawerClosed = vi.fn()
  const { rerender } = render(<SupportTab orgId={1} control show="open" onDone={vi.fn()}
                                          initialTicket={null} onDrawerClosed={onDrawerClosed} />)
  await screen.findByText('#7 Deposits')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  rerender(<SupportTab orgId={1} control show="open" onDone={vi.fn()} initialTicket={7}
                       onDrawerClosed={onDrawerClosed} />)
  const drawer = await screen.findByRole('dialog', { name: 'Ticket #7: Deposits' })
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(onDrawerClosed).toHaveBeenCalledTimes(1)
})
