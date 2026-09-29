// src/pages/Landing.test.tsx
import { existsSync, readFileSync } from 'node:fs'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import Landing, { FooterContact, LANDING_FACTS, PAGE_TITLE } from './Landing'

function renderLanding() {
  return render(<MemoryRouter><Landing /></MemoryRouter>)
}

test('the front page has one h1, offers sign in and account creation, and names its sections', () => {
  renderLanding()
  const h1s = screen.getAllByRole('heading', { level: 1 })
  expect(h1s).toHaveLength(1)
  expect(h1s[0]).toHaveTextContent(/trade once/i)

  const signIns = screen.getAllByRole('link', { name: 'Sign in' })
  expect(signIns.length).toBeGreaterThan(0)
  for (const a of signIns) expect(a).toHaveAttribute('href', '/login')

  const creates = screen.getAllByRole('link', { name: 'Create account' })
  expect(creates.length).toBeGreaterThan(1)
  for (const a of creates) expect(a).toHaveAttribute('href', '/register')

  for (const name of ['One desk for every account', 'How it works', 'For investors', 'Questions']) {
    expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument()
  }
  expect(screen.getByText(/high level of risk/i)).toBeInTheDocument()
})

test('the hero shows the product inside a glass frame', () => {
  renderLanding()
  const shot = screen.getByRole('img', { name: /MirrorFleet Overview/i })
  const frame = shot.closest('figure')
  expect(frame).not.toBeNull()
  expect(frame).toHaveClass('glass')
})

test('three asymmetric panels each show a piece of the product, marked as example data', () => {
  renderLanding()
  const fleet = screen.getByRole('heading', { level: 3, name: 'Every follower on one screen' }).closest('article')
  const log = screen.getByRole('heading', { level: 3, name: 'Every fill, timestamped' }).closest('article')
  const risk = screen.getByRole('heading', { level: 3, name: 'Limits before the broker' }).closest('article')
  expect(fleet).not.toBeNull()
  expect(log).not.toBeNull()
  expect(risk).not.toBeNull()

  // One wide panel, two narrow ones: the wide panel spans both rows so the
  // two narrow panels auto-place stacked in the remaining column.
  expect(fleet).toHaveClass('md:col-span-2')
  expect(fleet).toHaveClass('md:row-span-2')
  expect(log).not.toHaveClass('md:col-span-2')
  expect(risk).not.toHaveClass('md:col-span-2')

  // Real markup, not icons: follower cards with status words and equity,
  // timestamped log lines, a rule table.
  expect(within(fleet!).getByText('Offline')).toBeInTheDocument()
  expect(within(fleet!).getAllByText('Copying').length).toBeGreaterThan(1)
  expect(within(fleet!).getAllByText(/^[0-9,]+\.[0-9]{2}$/).length).toBeGreaterThan(3)
  expect(log!.querySelectorAll('time').length).toBeGreaterThan(3)
  expect(within(risk!).getByRole('table')).toBeInTheDocument()

  // The hero figure carries its own "example data" caption, so scope this
  // check to the platform section's own caption under the panels.
  const platform = document.getElementById('platform')
  expect(within(platform!).getByText(/example data, not trading results/i)).toBeInTheDocument()
})

test('the hero carries a visible example-data caption alongside its labelled image', () => {
  renderLanding()
  const shot = screen.getByRole('img', { name: /MirrorFleet Overview/i })
  const frame = shot.closest('figure')
  expect(within(frame!).getByText(/example data, not trading results/i)).toBeInTheDocument()
})

test('how it works is prose, with no numbered markers', () => {
  renderLanding()
  const how = document.getElementById('how')
  expect(how).not.toBeNull()
  expect(how!.querySelector('ol')).toBeNull()
  const prose = how!.querySelector('p')
  expect(prose?.textContent).toMatch(/connect.*add.*trade once/is)
})

test('the FAQ is a disclosure list', () => {
  renderLanding()
  const items = document.querySelectorAll('#faq details')
  expect(items.length).toBe(6)
  items.forEach((d) => {
    expect(d.querySelector('summary')).not.toBeNull()
    expect(d).not.toHaveAttribute('open')
  })
})

test('no eyebrow kickers: the small-caps label only appears on table headers', () => {
  renderLanding()
  expect(screen.queryByText(/copy trading desk/i)).toBeNull()
  const labels = Array.from(document.querySelectorAll('.desk-label'))
  for (const el of labels) expect(el.tagName).toBe('TH')
})

test('the public facts stay blank until the owner confirms them', () => {
  expect(LANDING_FACTS.supportEmail).toBe('')
  expect(LANDING_FACTS.address).toBe('')
})

test('the footer carries the risk notice, and neither contact line while the facts are blank', () => {
  renderLanding()
  const footer = screen.getByRole('contentinfo')
  expect(within(footer).getByText(/high level of risk/i)).toBeInTheDocument()
  expect(within(footer).queryByRole('link', { name: /@/ })).toBeNull()
  expect(footer.querySelector('a[href^="mailto:"]')).toBeNull()
  expect(footer.querySelector('address')).toBeNull()
})

test('the footer carries the support email and the address once they are filled', () => {
  render(<FooterContact facts={{ ...LANDING_FACTS, address: 'Somewhere', supportEmail: 'help@desk.example' }} />)
  expect(screen.getByRole('link', { name: 'help@desk.example' })).toHaveAttribute('href', 'mailto:help@desk.example')
  expect(screen.getByText(/Somewhere/)).toBeInTheDocument()
})

test('the footer omits the email and address when they are empty', () => {
  const { container } = render(<FooterContact facts={{ ...LANDING_FACTS, address: '', supportEmail: '' }} />)
  expect(container).toBeEmptyDOMElement()
})

test('the page sets its own title and hands the old one back on unmount', () => {
  document.title = 'Before'
  const { unmount } = renderLanding()
  expect(document.title).toBe('MirrorFleet — copy trading for cTrader and MT5')
  expect(PAGE_TITLE.length).toBeLessThan(60)
  unmount()
  expect(document.title).toBe('Before')
})

test('index.html describes the product in one honest sentence', () => {
  const html = readFileSync('index.html', 'utf8')
  const m = html.match(/<meta name="description" content="([^"]+)" \/>/)
  expect(m).not.toBeNull()
  const description = m![1]
  expect(description.length).toBeGreaterThan(50)
  expect(description.length).toBeLessThanOrEqual(160)
  expect(description).toMatch(/cTrader/)
  expect(description).toMatch(/MetaTrader 5/)
  expect(description).not.toMatch(/regulated|licensed|award|guarantee|best|#1/i)
})

test('the landing sources use tokens, never hard-coded colours', () => {
  const files = ['src/pages/Landing.tsx', 'src/pages/landing/HeroPreview.tsx'].filter((f) => existsSync(f))
  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(|hsla?\(/)
  }
})

test('the page makes no claims it cannot back', () => {
  renderLanding()
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/regulated|licensed|award|guarantee|risk-free|[0-9,]+\+? (clients|traders|users)/i)
})
