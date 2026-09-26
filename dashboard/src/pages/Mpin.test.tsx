import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import Mpin from './Mpin'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

function json(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'content-type': 'application/json' },
  })
}

function renderMpin(path = '/mpin') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/mpin" element={<Mpin />} />
        <Route path="/" element={<div>desk root</div>} />
        <Route path="/welcome" element={<div>welcome</div>} />
        <Route path="/join/:token" element={<div>join page</div>} />
        <Route path="/login" element={<div>login page</div>} />
      </Routes>
    </MemoryRouter>
  )
}

function stub(me: unknown, handlers: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/me') return json(me)
    const h = handlers[url]
    if (h) return h(init)
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function typePin(pin: string) {
  const first = document.querySelector<HTMLInputElement>('input')!
  first.focus()
  await userEvent.keyboard(pin)
}

test('Verify mode: six digits submit, success goes to next', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json(null, 204),
  })
  renderMpin('/mpin?next=%2Fwelcome')
  expect(await screen.findByRole('heading', { name: 'Enter your MPIN' })).toBeInTheDocument()
  await typePin('123456')
  await waitFor(() => expect(screen.getByText('welcome')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/verify')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ mpin: '123456' })
})

test('a wrong MPIN shows the tries left and clears the boxes', async () => {
  stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json({ detail: 'Invalid MPIN', attempts_left: 3 }, 401),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('000000')
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 3 tries left')
  expect(document.querySelector<HTMLInputElement>('input')!.value).toBe('')
})

test('shows the singular copy when one try is left', async () => {
  stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json({ detail: 'Invalid MPIN', attempts_left: 1 }, 401),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('000000')
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 1 try left')
})

test('a lock disables entry and counts down; Forgot MPIN still works', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const until = new Date(Date.now() + 90_000).toISOString()
  stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json({ detail: 'MPIN locked', locked_until: until }, 423),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('000000')
  expect(await screen.findByText(/Locked\. Try again in 01:[23]\d/)).toBeInTheDocument()
  expect(document.querySelector<HTMLInputElement>('input')).toBeDisabled()
  expect(screen.getByRole('button', { name: /forgot mpin/i })).toBeEnabled()
})

test('Set mode asks twice and posts to /api/mpin/set', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: false } }, {
    '/api/mpin/set': () => json(null, 204),
  })
  renderMpin()
  expect(await screen.findByRole('heading', { name: 'Choose your MPIN' })).toBeInTheDocument()
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[0].focus(); await userEvent.keyboard('246810')
  inputs()[6].focus(); await userEvent.keyboard('246810')
  await userEvent.click(screen.getByRole('button', { name: /save mpin/i }))
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/set')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ mpin: '246810', mpin_confirm: '246810' })
})

test('Set mode refuses a mismatch before calling the server', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: false } })
  renderMpin()
  await screen.findByRole('heading', { name: 'Choose your MPIN' })
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[0].focus(); await userEvent.keyboard('246810')
  inputs()[6].focus(); await userEvent.keyboard('111111')
  await userEvent.click(screen.getByRole('button', { name: /save mpin/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent('MPINs do not match')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/mpin/set')).toBe(false)
})

test('Forgot mode posts the password with the new MPIN', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/reset': () => json(null, 204),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await userEvent.click(screen.getByRole('button', { name: /forgot mpin/i }))
  expect(screen.getByRole('heading', { name: 'Reset your MPIN' })).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText(/^password$/i), 'a-solid-password')
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[1].focus(); await userEvent.keyboard('999999')
  inputs()[7].focus(); await userEvent.keyboard('999999')
  await userEvent.click(screen.getByRole('button', { name: /reset mpin/i }))
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/reset')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({
    password: 'a-solid-password', mpin: '999999', mpin_confirm: '999999',
  })
})

test('Sign out is available and goes to /login', async () => {
  stub({ mpin: { pending: true, set: true } }, { '/api/logout': () => json(null, 204) })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await userEvent.click(screen.getByRole('button', { name: /sign out/i }))
  await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
})

test('a session that is not pending bounces to /; no session bounces to /login', async () => {
  stub({ user: { id: 1, email: 'a@x', display_name: 'A' }, orgs: [], mpin: { pending: false, set: true } })
  const { unmount } = renderMpin()
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  unmount()
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Not authenticated' }, 401)))
  renderMpin()
  await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
})

test('next must be a same-origin path; anything else falls back to /', async () => {
  stub({ mpin: { pending: true, set: true } }, { '/api/mpin/verify': () => json(null, 204) })
  for (const next of ['https%3A%2F%2Fevil.example', '%2F%5Cevil.com']) {
    const view = renderMpin(`/mpin?next=${next}`)
    await screen.findByRole('heading', { name: 'Enter your MPIN' })
    await typePin('123456')
    await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
    view.unmount()
  }
})
