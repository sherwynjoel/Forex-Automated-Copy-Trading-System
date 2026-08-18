import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom'
import { expect, test, vi, afterEach } from 'vitest'
import Join from './Join'

afterEach(() => vi.unstubAllGlobals())

function OrgLanding() {
  const { orgId } = useParams()
  return <div>org home {orgId}</div>
}

function renderJoin(token: string) {
  return render(
    <MemoryRouter initialEntries={[`/join/${token}`]}>
      <Routes>
        <Route path="/join/:token" element={<Join />} />
        <Route path="/org/:orgId" element={<OrgLanding />} />
      </Routes>
    </MemoryRouter>
  )
}

function stubFetch(overrides: { me?: Response; join?: Response }) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') {
      return Promise.resolve(
        overrides.me ??
          new Response(
            JSON.stringify({
              user: { id: 1, email: 'a@example.com', display_name: 'A' },
              orgs: [],
            }),
            { status: 200, headers: { 'content-type': 'application/json' } }
          )
      )
    }
    if (url === '/api/orgs/join') {
      if (!overrides.join) throw new Error('Unexpected /api/orgs/join call')
      return Promise.resolve(overrides.join)
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('posts the route token to /api/orgs/join and navigates to the org on success', async () => {
  const fetchMock = stubFetch({
    join: new Response(JSON.stringify({ org_id: 42, role: 'trader' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  })

  renderJoin('abc123')

  await waitFor(() => {
    expect(screen.getByText('org home 42')).toBeInTheDocument()
  })
  const joinCall = fetchMock.mock.calls.find(([u]) => String(u) === '/api/orgs/join')
  expect(joinCall).toBeTruthy()
  expect(JSON.parse((joinCall![1] as RequestInit).body as string)).toEqual({ token: 'abc123' })
})

test('shows an invalid/expired message on 410', async () => {
  stubFetch({ join: new Response('Gone', { status: 410 }) })

  renderJoin('deadtoken')

  await waitFor(() => {
    expect(screen.getByText(/invalid.*expired/i)).toBeInTheDocument()
  })
})
