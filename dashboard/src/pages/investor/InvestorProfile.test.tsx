import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import InvestorProfile from './InvestorProfile'
import { mockUseOrg } from '../../test/orgMock'
import { profileFixture } from '../../test/portalFixtures'
import type { KycProfile } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const REQUIRED = ['full_name', 'gender', 'date_of_birth', 'phone', 'address_line', 'city', 'postal_code',
  'country_residence', 'country_citizenship', 'id_type', 'id_number', 'id_front_file_id', 'id_back_file_id',
  'address_proof_file_id', 'photo_file_id'] as const
const CONTACT = new Set(['phone', 'address_line', 'area', 'landmark', 'city', 'state', 'postal_code'])

const empty = profileFixture({
  full_name: null, gender: null, date_of_birth: null, phone: null, address_line: null, city: null,
  state: null, postal_code: null, country_residence: null, country_citizenship: null, id_type: null,
  id_number: null, id_front_file_id: null, id_back_file_id: null, address_proof_file_id: null,
  photo_file_id: null, missing: [...REQUIRED],
})

/** A tiny server: PUT merges and recomputes `missing`, an approved profile
 *  drops to draft on any non-contact change, submit locks it. */
function mockRoutes(start: KycProfile) {
  let profile: KycProfile = { ...start }
  let uploads = 90
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') return jsonResponse(profile)
    if (url.endsWith('/investor/profile') && method === 'PUT') {
      const body = JSON.parse(String(init!.body)) as Partial<KycProfile>
      const reverify = profile.status === 'approved' && Object.keys(body).some((k) => !CONTACT.has(k))
      profile = { ...profile, ...body, status: reverify ? 'draft' : profile.status }
      profile.missing = REQUIRED.filter((k) => profile[k] == null)
      return jsonResponse(profile)
    }
    if (url.endsWith('/investor/files') && method === 'POST') {
      uploads += 1
      return jsonResponse({ id: uploads, purpose: (init!.body as FormData).get('purpose'),
                            content_type: 'image/png', size_bytes: 3, created_at: '2026-10-01T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/profile/submit') && method === 'POST') {
      profile = { ...profile, status: 'submitted', submitted_at: '2026-10-01T10:00:00Z' }
      return jsonResponse(profile)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const calls = (fetchMock: ReturnType<typeof mockRoutes>, tail: string, method: string) =>
  fetchMock.mock.calls.filter(([u, init]) => String(u).endsWith(tail) && (init as RequestInit | undefined)?.method === method)

function renderPage() {
  return render(<MemoryRouter><InvestorProfile /></MemoryRouter>)
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('a new investor sees an empty draft, the five steps and the title', async () => {
  mockRoutes(empty)
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Profile & verification' })).toBeInTheDocument()
  expect(await screen.findByText('Not submitted')).toBeInTheDocument()
  const steps = within(screen.getByRole('list', { name: 'Verification steps' })).getAllByRole('button')
  expect(steps.map((b) => b.textContent)).toEqual(['1. Profile', '2. Identity', '3. Address', '4. Photo', '5. Review'])
  expect(steps[0]).toHaveAttribute('aria-current', 'step')
  expect(screen.getByLabelText('Full name')).toHaveValue('')
  expect(document.title).toBe('Profile & verification · MirrorFleet')
})

test('Save and continue sends only the changed fields of the step and moves on', async () => {
  const fetchMock = mockRoutes(empty)
  renderPage()
  await userEvent.type(await screen.findByLabelText('Full name'), 'Ada Lovelace')
  await userEvent.selectOptions(screen.getByLabelText('Gender'), 'female')
  await userEvent.type(screen.getByLabelText('Date of birth'), '1990-04-02')
  await userEvent.type(screen.getByLabelText('Phone'), '+44 20 1234')
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(1))
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile', 'PUT')[0][1] as RequestInit).body))).toEqual({
    full_name: 'Ada Lovelace', gender: 'female', date_of_birth: '1990-04-02', phone: '+44 20 1234',
  })
  await waitFor(() => expect(screen.getByRole('button', { name: '2. Identity' })).toHaveAttribute('aria-current', 'step'))
  expect(screen.getByText('Saved')).toBeInTheDocument()
})

test('a document is uploaded with its KYC purpose and saved by id', async () => {
  const fetchMock = mockRoutes({ ...profileFixture(), id_front_file_id: null, missing: ['id_front_file_id'] })
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '2. Identity' }))
  await userEvent.upload(screen.getByLabelText('ID front'), new File(['x'], 'front.png', { type: 'image/png' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(1))
  const upload = calls(fetchMock, '/investor/files', 'POST')[0]
  expect((upload[1] as RequestInit).body instanceof FormData).toBe(true)
  expect(((upload[1] as RequestInit).body as FormData).get('purpose')).toBe('kyc_document')
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile', 'PUT')[0][1] as RequestInit).body)))
    .toEqual({ id_front_file_id: 91 })
  await waitFor(() => expect(screen.getByRole('button', { name: '3. Address' })).toHaveAttribute('aria-current', 'step'))
})

test('Review lists what is still needed and holds the submit back', async () => {
  mockRoutes({ ...profileFixture(), photo_file_id: null, missing: ['photo_file_id'] })
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  expect(screen.getByText('Still needed: Your photo')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Submit for verification' })).toBeDisabled()
  expect(screen.getByText('Passport')).toBeInTheDocument()
})

test('a complete draft is submitted with the MPIN and then locked', async () => {
  const fetchMock = mockRoutes(profileFixture())
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit for verification' }))
  const dialog = await screen.findByRole('dialog', { name: 'Submit your profile for verification?' })
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(JSON.parse(String((calls(fetchMock, '/investor/profile/submit', 'POST')[0][1] as RequestInit).body)))
    .toEqual({ mpin: '123456' })
  expect(screen.getByText('Under review')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '1. Profile' }))
  expect(screen.getByLabelText('Full name')).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Save and continue' })).not.toBeInTheDocument()
})

test('a rejected profile shows the admin note', async () => {
  mockRoutes(profileFixture({ status: 'rejected', decision_note: 'ID photo is blurred' }))
  renderPage()
  expect(await screen.findByText('Rejected: ID photo is blurred. Fix what the note asks and submit again.'))
    .toBeInTheDocument()
})

test('an approved investor who changes their name is told to submit again', async () => {
  mockRoutes(profileFixture({ status: 'approved' }))
  renderPage()
  const name = await screen.findByLabelText('Full name')
  await userEvent.clear(name)
  await userEvent.type(name, 'Sherwyn J')
  await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
  expect(await screen.findByText('Saved. You changed identity details, so submit again to be verified.'))
    .toBeInTheDocument()
  expect(screen.getByText('Not submitted')).toBeInTheDocument()
})

test('a 409 on save reloads the profile and keeps the conflict visible', async () => {
  const reloaded = profileFixture({ full_name: 'Changed Elsewhere' })
  let getCount = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') {
      getCount += 1
      return jsonResponse(getCount === 1 ? profileFixture() : reloaded)
    }
    if (url.endsWith('/investor/profile') && method === 'PUT') {
      return jsonResponse({ detail: 'your profile changed; reload it' }, 409)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  const name = await screen.findByLabelText('Full name')
  await userEvent.clear(name)
  await userEvent.type(name, 'My Edit')
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  expect(await screen.findByText('your profile changed; reload it')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByLabelText('Full name')).toHaveValue('Changed Elsewhere'))
  expect(getCount).toBe(2)
  expect(screen.getByRole('button', { name: '1. Profile' })).toHaveAttribute('aria-current', 'step')
})

test('a 409 on submit reloads the profile and the dialog still shows the conflict', async () => {
  const reloaded = profileFixture({ full_name: 'Changed Elsewhere' })
  let getCount = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') {
      getCount += 1
      return jsonResponse(getCount === 1 ? profileFixture() : reloaded)
    }
    if (url.endsWith('/investor/profile/submit') && method === 'POST') {
      return jsonResponse({ detail: 'your profile changed; reload it' }, 409)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit for verification' }))
  const dialog = await screen.findByRole('dialog', { name: 'Submit your profile for verification?' })
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))
  expect(await within(dialog).findByText('your profile changed; reload it')).toBeInTheDocument()
  await waitFor(() => expect(getCount).toBe(2))
})

test('a 400 on submit shows the still-missing fields by their labels', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') return jsonResponse(profileFixture())
    if (url.endsWith('/investor/profile/submit') && method === 'POST') {
      return jsonResponse({ detail: 'complete your profile first: full_name, date_of_birth',
                            missing: ['full_name', 'date_of_birth'] }, 400)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '5. Review' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit for verification' }))
  const dialog = await screen.findByRole('dialog', { name: 'Submit your profile for verification?' })
  await userEvent.click(within(dialog).getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard('123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))
  expect(await within(dialog).findByText('complete your profile first: Full name, Date of birth'))
    .toBeInTheDocument()
})

test('a save that fails after uploading reuses the id on retry instead of re-uploading', async () => {
  const start = { ...profileFixture(), id_front_file_id: null, missing: ['id_front_file_id'] }
  let profile: KycProfile = { ...start }
  let uploads = 90
  let putAttempts = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') return jsonResponse(profile)
    if (url.endsWith('/investor/files') && method === 'POST') {
      uploads += 1
      return jsonResponse({ id: uploads, purpose: (init!.body as FormData).get('purpose'),
                            content_type: 'image/png', size_bytes: 3, created_at: '2026-10-01T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/profile') && method === 'PUT') {
      putAttempts += 1
      if (putAttempts === 1) return jsonResponse({ detail: 'server hiccup' }, 500)
      const body = JSON.parse(String(init!.body)) as Partial<KycProfile>
      profile = { ...profile, ...body, missing: [] }
      return jsonResponse(profile)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '2. Identity' }))
  await userEvent.upload(screen.getByLabelText('ID front'), new File(['x'], 'front.png', { type: 'image/png' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  expect(await screen.findByText('server hiccup')).toBeInTheDocument()
  expect(calls(fetchMock, '/investor/files', 'POST')).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '3. Address' })).toHaveAttribute('aria-current', 'step'))
  expect(calls(fetchMock, '/investor/files', 'POST')).toHaveLength(1)
  const puts = calls(fetchMock, '/investor/profile', 'PUT')
  expect(puts).toHaveLength(2)
  expect(JSON.parse(String((puts[1][1] as RequestInit).body))).toEqual({ id_front_file_id: 91 })
})

test('saving another step keeps a pending upload from the step that failed', async () => {
  let profile: KycProfile = { ...profileFixture(), id_front_file_id: null, photo_file_id: null,
                              missing: ['id_front_file_id', 'photo_file_id'] }
  let uploads = 90
  let putAttempts = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/investor/profile') && method === 'GET') return jsonResponse(profile)
    if (url.endsWith('/investor/files') && method === 'POST') {
      uploads += 1
      return jsonResponse({ id: uploads, purpose: (init!.body as FormData).get('purpose'),
                            content_type: 'image/png', size_bytes: 3, created_at: '2026-10-01T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/profile') && method === 'PUT') {
      putAttempts += 1
      if (putAttempts === 1) return jsonResponse({ detail: 'server hiccup' }, 500)
      const body = JSON.parse(String(init!.body)) as Partial<KycProfile>
      profile = { ...profile, ...body }
      return jsonResponse(profile)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  renderPage()
  await userEvent.click(await screen.findByRole('button', { name: '2. Identity' }))
  await userEvent.upload(screen.getByLabelText('ID front'), new File(['x'], 'front.png', { type: 'image/png' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  expect(await screen.findByText('server hiccup')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '4. Photo' }))
  await userEvent.upload(screen.getByLabelText('Your photo'), new File(['y'], 'me.png', { type: 'image/png' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(2))
  await userEvent.click(await screen.findByRole('button', { name: '2. Identity' }))
  await userEvent.click(screen.getByRole('button', { name: 'Save and continue' }))
  await waitFor(() => expect(calls(fetchMock, '/investor/profile', 'PUT')).toHaveLength(3))
  const puts = calls(fetchMock, '/investor/profile', 'PUT')
  expect(JSON.parse(String((puts[2][1] as RequestInit).body))).toEqual({ id_front_file_id: 91 })
  expect(calls(fetchMock, '/investor/files', 'POST')).toHaveLength(2)
})
