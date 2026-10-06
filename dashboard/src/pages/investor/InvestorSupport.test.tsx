import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorSupport from './InvestorSupport'
import { mockUseOrg } from '../../test/orgMock'
import {
  subjectFixture, threadFixture, ticketFixture, ticketMessageFixture,
} from '../../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const subjects = [subjectFixture({ id: 2, label: 'Deposits' }), subjectFixture({ id: 3, label: 'Withdrawals', sort: 1 })]
const t7 = ticketFixture({ id: 7, subject_label: 'Deposits', status: 'new' })
const t8 = ticketFixture({ id: 8, subject_label: 'Withdrawals', status: 'closed', closed_at: '2026-10-02T10:00:00Z' })
const thread7 = threadFixture({
  id: 7, subject_label: 'Deposits',
  messages: [
    ticketMessageFixture({ id: 70, body: 'My deposit has not arrived.', file_ids: [41] }),
    ticketMessageFixture({ id: 71, from_desk: true, author_name: 'Desk Admin', body: 'We are checking.' }),
  ],
})

function mockRoutes(opts: { slowAll?: Promise<Response>; raiseFailsOnce?: boolean } = {}) {
  let raiseFails = opts.raiseFailsOnce ?? false
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/ticket-subjects')) return jsonResponse(subjects)
    if (url.endsWith('/investor/files') && method === 'POST') {
      return jsonResponse({ id: 91, purpose: 'ticket_attachment', content_type: 'image/png',
                            size_bytes: 3, created_at: '2026-10-05T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/tickets') && method === 'POST') {
      if (raiseFails) {
        raiseFails = false
        return jsonResponse({ detail: 'too many requests; try again later' }, 429)
      }
      return jsonResponse({ ...thread7, id: 9, subject_label: 'Withdrawals' }, 201)
    }
    if (url.endsWith('/investor/tickets/7/messages') && method === 'POST') {
      const body = JSON.parse(init!.body as string).body
      return jsonResponse({ ...thread7, messages: [...thread7.messages, ticketMessageFixture({ id: 72, body })] }, 201)
    }
    if (url.endsWith('/investor/tickets/7/close') && method === 'POST') {
      return jsonResponse({ ...thread7, status: 'closed', closed_at: '2026-10-05T10:00:00Z' })
    }
    if (url.endsWith('/investor/tickets/9')) return jsonResponse({ ...thread7, id: 9, subject_label: 'Withdrawals' })
    if (url.endsWith('/investor/tickets/7')) return jsonResponse(thread7)
    if (url.includes('/investor/tickets?status=closed')) return jsonResponse([t8])
    if (url.includes('/investor/tickets?q=')) return jsonResponse([t7])
    if (url.endsWith('/investor/tickets')) return opts.slowAll ?? jsonResponse([t7, t8])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).endsWith(fragment) && (init as RequestInit | undefined)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

function renderPage(entry = '/org/1/invest/support') {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes><Route path="/org/1/invest/support" element={<InvestorSupport />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists the tickets with status tabs and a search', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  expect(await screen.findByRole('heading', { level: 1, name: 'Support' })).toBeInTheDocument()
  expect(await screen.findByText('Deposits')).toBeInTheDocument()
  expect(screen.getByText('Withdrawals')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Closed' }))
  await waitFor(() => expect(screen.queryByText('Deposits')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/investor/tickets?status=closed'))).toBe(true)
  await userEvent.click(screen.getByRole('tab', { name: 'All' }))
  await userEvent.type(screen.getByLabelText('Search tickets'), 'usdt')
  await userEvent.click(screen.getByRole('button', { name: 'Search' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investor/tickets?q=usdt'))).toBe(true))
})

test('raises a ticket with an image and opens its thread', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('Deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Raise ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Raise a ticket' })
  await userEvent.selectOptions(within(dialog).getByLabelText('Subject'), '3')
  await userEvent.type(within(dialog).getByLabelText('Message'), 'Withdrawal stuck')
  await userEvent.upload(within(dialog).getByLabelText('Image 1 (optional)'),
    new File(['png'], 'shot.png', { type: 'image/png' }))
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor/tickets', 'POST'))
    .toEqual({ subject_id: 3, body: 'Withdrawal stuck', file_ids: [91] }))
  expect(await screen.findByText('Ticket sent. The desk replies here and in your notifications.')).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: '#9 Withdrawals' })).toBeInTheDocument()
})

test('the thread shows the messages and images, sends a reply and closes', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/invest/support?ticket=7')
  expect(await screen.findByRole('heading', { name: '#7 Deposits' })).toBeInTheDocument()
  expect(screen.getByText('My deposit has not arrived.')).toBeInTheDocument()
  expect(screen.getByText('Support desk')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Image 1' })).toHaveAttribute('src', '/api/orgs/1/investor/files/41')
  await userEvent.type(screen.getByLabelText('Your reply'), 'Thanks')
  await userEvent.click(screen.getByRole('button', { name: 'Send reply' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor/tickets/7/messages', 'POST'))
    .toEqual({ body: 'Thanks', file_ids: [] }))
  expect(await screen.findByText('Reply sent.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Close ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Close ticket #7?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, close it' }))
  expect(await screen.findByText('Ticket closed.')).toBeInTheDocument()
  expect(screen.getByText('This ticket is closed. A reply opens it again.')).toBeInTheDocument()
})

test('a failed send keeps the uploaded image, so the retry does not upload it again', async () => {
  const fetchMock = mockRoutes({ raiseFailsOnce: true })
  renderPage()
  await screen.findByText('Deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Raise ticket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Raise a ticket' })
  await userEvent.type(within(dialog).getByLabelText('Message'), 'Withdrawal stuck')
  await userEvent.upload(within(dialog).getByLabelText('Image 1 (optional)'),
    new File(['png'], 'shot.png', { type: 'image/png' }))
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  expect(await within(dialog).findByText(/too many requests/)).toBeInTheDocument()
  expect(within(dialog).getByText('Uploaded; it goes with your message')).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send ticket' }))
  expect(await screen.findByText('Ticket sent. The desk replies here and in your notifications.')).toBeInTheDocument()
  const posts = (fragment: string) => fetchMock.mock.calls.filter(([u, i]) =>
    String(u).endsWith(fragment) && (i as RequestInit | undefined)?.method === 'POST')
  expect(posts('/investor/files')).toHaveLength(1)
  expect(posts('/investor/tickets').map(([, i]) => JSON.parse((i as RequestInit).body as string).file_ids))
    .toEqual([[91], [91]])
})

test('a slow list for an older filter never lands over the newer one', async () => {
  let release!: (r: Response) => void
  mockRoutes({ slowAll: new Promise<Response>((res) => { release = res }) })
  renderPage()
  await userEvent.click(await screen.findByRole('tab', { name: 'Closed' }))
  expect(await screen.findByText('Withdrawals')).toBeInTheDocument()
  await act(async () => { release(jsonResponse([t7, t8])) })
  expect(screen.queryByText('Deposits')).not.toBeInTheDocument()
})
