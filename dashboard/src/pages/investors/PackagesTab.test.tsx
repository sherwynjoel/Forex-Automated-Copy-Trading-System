import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import PackagesTab from './PackagesTab'
import { packageFixture } from '../../test/portalFixtures'
import type { AccountPackage } from '../../lib/types'

function jsonResponse(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function mockRoutes(opts: { deleteRefused?: boolean } = {}) {
  let rows: AccountPackage[] = [packageFixture()]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/account-packages') && method === 'GET') return jsonResponse(rows)
    if (url.endsWith('/account-packages') && method === 'POST') {
      const body = JSON.parse(String(init!.body)) as Partial<AccountPackage>
      const created = packageFixture({ ...body, id: 2, min_deposit: Number(body.min_deposit) })
      rows = [...rows, created]
      return jsonResponse(created, 201)
    }
    const m = url.match(/\/account-packages\/(\d+)$/)
    if (m && method === 'PATCH') {
      const body = JSON.parse(String(init!.body)) as Partial<AccountPackage>
      rows = rows.map((r) => r.id === Number(m[1]) ? { ...r, ...body } : r)
      return jsonResponse(rows.find((r) => r.id === Number(m[1])))
    }
    if (m && method === 'DELETE') {
      if (opts.deleteRefused) return jsonResponse({ detail: 'an open request still uses this package' }, 409)
      rows = rows.filter((r) => r.id !== Number(m[1]))
      return jsonResponse(null, 204)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof mockRoutes>, method: string) =>
  JSON.parse(String((fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === method)![1] as RequestInit).body))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists packages with their terms', async () => {
  mockRoutes()
  render(<PackagesTab orgId={1} control />)
  expect(await screen.findByText('Standard')).toBeInTheDocument()
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByText('1:100 · 1:200 · 1:500')).toBeInTheDocument()
  expect(screen.getByText('Enabled')).toBeInTheDocument()
})

test('adds a package from the drawer with a parsed leverage list', async () => {
  const fetchMock = mockRoutes()
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Add package' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add account package' })
  await userEvent.type(within(drawer).getByLabelText('Name'), 'Pro')
  await userEvent.clear(within(drawer).getByLabelText('Minimum deposit'))
  await userEvent.type(within(drawer).getByLabelText('Minimum deposit'), '1000')
  await userEvent.clear(within(drawer).getByLabelText('Leverage options'))
  await userEvent.type(within(drawer).getByLabelText('Leverage options'), '200, 100')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save package' }))
  expect(await screen.findByText('Pro')).toBeInTheDocument()
  expect(bodyOf(fetchMock, 'POST')).toEqual({
    name: 'Pro', min_deposit: '1000', currency: 'USD', spread_label: '', leverage_options: [200, 100], sort_order: 0,
  })
})

test('a bad leverage list is refused in the drawer', async () => {
  const fetchMock = mockRoutes()
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Add package' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add account package' })
  await userEvent.type(within(drawer).getByLabelText('Name'), 'Pro')
  await userEvent.clear(within(drawer).getByLabelText('Leverage options'))
  await userEvent.type(within(drawer).getByLabelText('Leverage options'), '1:100')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save package' }))
  expect(await within(drawer).findByText('Leverage options: whole numbers from 1 to 3000, separated by commas')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === 'POST')).toBe(false)
})

test('disables a package and refuses a delete the server refuses', async () => {
  const fetchMock = mockRoutes({ deleteRefused: true })
  render(<PackagesTab orgId={1} control />)
  await userEvent.click(await screen.findByRole('button', { name: 'Disable Standard' }))
  expect(await screen.findByText('Disabled')).toBeInTheDocument()
  expect(bodyOf(fetchMock, 'PATCH')).toEqual({ enabled: false })
  await userEvent.click(screen.getByRole('button', { name: 'Delete Standard' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete Standard?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
  expect(await screen.findByText('an open request still uses this package')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('a viewer sees the packages but no actions', async () => {
  mockRoutes()
  render(<PackagesTab orgId={1} control={false} />)
  expect(await screen.findByText('Standard')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Add package' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Edit Standard' })).not.toBeInTheDocument()
})
