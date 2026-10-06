import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import TicketSubjectsCard from './TicketSubjectsCard'
import { subjectFixture } from '../../test/portalFixtures'
import type { TicketSubject } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(refuse?: { status: number; body: unknown }) {
  let subjects: TicketSubject[] = [subjectFixture({ id: 2, label: 'Deposits' })]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : {}
    if (url.endsWith('/ticket-subjects') && method === 'POST') {
      if (refuse) return jsonResponse(refuse.body, refuse.status)
      const s = subjectFixture({ id: 3, ...body })
      subjects = [...subjects, s]
      return jsonResponse(s, 201)
    }
    const m = /\/ticket-subjects\/(\d+)$/.exec(url)
    if (m && method === 'PATCH') {
      subjects = subjects.map((s) => (s.id === Number(m[1]) ? { ...s, ...body } : s))
      return jsonResponse(subjects.find((s) => s.id === Number(m[1])))
    }
    if (m && method === 'DELETE') {
      subjects = subjects.filter((s) => s.id !== Number(m[1]))
      return new Response(null, { status: 204 })
    }
    if (url.endsWith('/ticket-subjects')) return jsonResponse(subjects)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodies = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) =>
  fetchMock.mock.calls
    .filter(([u, i]) => String(u).endsWith(fragment) && (i as RequestInit | undefined)?.method === method)
    .map(([, i]) => JSON.parse((i as RequestInit).body as string))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('adds, renames, disables and deletes subjects', async () => {
  const fetchMock = mockRoutes()
  render(<TicketSubjectsCard orgId={1} control />)
  expect(await screen.findByText('Deposits')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('New subject'), 'Withdrawals')
  await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
  await waitFor(() => expect(bodies(fetchMock, '/ticket-subjects', 'POST')).toEqual([{ label: 'Withdrawals', sort: 1 }]))
  expect(await screen.findByText('Subject added')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Rename Deposits' }))
  const name = screen.getByLabelText('New name for Deposits')
  await userEvent.clear(name)
  await userEvent.type(name, 'Funding')
  await userEvent.click(screen.getByRole('button', { name: 'Save name' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Disable Funding' }))
  await waitFor(() => expect(bodies(fetchMock, '/ticket-subjects/2', 'PATCH'))
    .toEqual([{ label: 'Funding' }, { enabled: false }]))
  expect(await screen.findByText('Disabled')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Delete Withdrawals' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete the subject Withdrawals?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete subject' }))
  await waitFor(() => expect(screen.queryByText('Withdrawals')).not.toBeInTheDocument())
  expect(screen.getByText('Subject deleted')).toBeInTheDocument()
})

test("the server's refusal shows inside the card", async () => {
  mockRoutes({ status: 409, body: { detail: 'a subject with this label already exists' } })
  render(<TicketSubjectsCard orgId={1} control />)
  await screen.findByText('Deposits')
  await userEvent.type(screen.getByLabelText('New subject'), 'deposits')
  await userEvent.click(screen.getByRole('button', { name: 'Add subject' }))
  expect(await screen.findByText('a subject with this label already exists')).toBeInTheDocument()
})

test('a viewer sees the subjects but no controls', async () => {
  mockRoutes()
  render(<TicketSubjectsCard orgId={1} control={false} />)
  await screen.findByText('Deposits')
  expect(screen.queryByRole('button', { name: 'Add subject' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Rename Deposits' })).not.toBeInTheDocument()
})
